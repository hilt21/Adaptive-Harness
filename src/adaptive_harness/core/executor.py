"""Trusted local command execution and evidence capture."""

from __future__ import annotations

import fcntl
import hashlib
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import TextIO

from adaptive_harness.core.envelope import TaskEnvelope
from adaptive_harness.core.gateway import (
    AuthorizedCommand,
    NetworkAccess,
    WorkspaceSnapshot,
)
from adaptive_harness.core.store import TaskEvent, TaskRecord, TaskStore


class ExecutorError(RuntimeError):
    """Base class for trusted execution failures."""


class ExecutionRejectedError(ExecutorError):
    """Raised before execution when authorization evidence is not trustworthy."""


class LeaseConflictError(ExecutorError):
    """Raised when another task owns the workspace execution lease."""


@dataclass(frozen=True, slots=True)
class ExecutionLimits:
    """Hard local resource limits independent from model instructions."""

    max_output_bytes: int = 1_048_576
    summary_characters: int = 2_000
    poll_interval_seconds: float = 0.05
    termination_grace_seconds: float = 0.25

    def __post_init__(self) -> None:
        if type(self.max_output_bytes) is not int or self.max_output_bytes < 1:
            raise ValueError("max_output_bytes must be a positive integer")
        if type(self.summary_characters) is not int or self.summary_characters < 1:
            raise ValueError("summary_characters must be a positive integer")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.termination_grace_seconds <= 0:
            raise ValueError("termination_grace_seconds must be positive")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Verified result and artifact references from one execution attempt."""

    task_id: str
    capability_id: str
    status: str
    exit_code: int | None
    timed_out: bool
    cancelled: bool
    output_truncated: bool
    stdout_artifact: Path
    stderr_artifact: Path
    authorization_event_sequence: int
    attempt: int


class WorkspaceLease:
    """A non-blocking process lease keyed by canonical worktree path."""

    def __init__(self, lease_dir: Path, worktree_path: Path, task_id: str) -> None:
        digest = hashlib.sha256(str(worktree_path).encode("utf-8")).hexdigest()
        self._lease_dir = Path(lease_dir)
        self._path = self._lease_dir / f"{digest}.lock"
        self._task_id = task_id
        self._handle: TextIO | None = None

    def __enter__(self) -> WorkspaceLease:
        self._lease_dir.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise LeaseConflictError(
                f"workspace lease is held by another task: {self._path}"
            ) from error
        handle.seek(0)
        handle.truncate()
        handle.write(f"{self._task_id}\n")
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        handle = self._handle
        if handle is None:
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
        self._handle = None


class Executor:
    """Execute only canonical Gateway authorizations without a shell."""

    def __init__(
        self,
        *,
        store: TaskStore,
        artifact_root: Path,
        workspace_probe: Callable[[], WorkspaceSnapshot],
        limits: ExecutionLimits,
        secrets: tuple[str, ...] = (),
    ) -> None:
        if any(not isinstance(secret, str) or not secret for secret in secrets):
            raise ValueError("secrets must contain non-empty strings")
        self._store = store
        self._artifact_root = Path(artifact_root)
        self._workspace_probe = workspace_probe
        self._limits = limits
        self._secrets = tuple(sorted(set(secrets), key=len, reverse=True))

    def execute(
        self,
        authorization: AuthorizedCommand,
        *,
        cancellation: Event | None = None,
    ) -> CommandResult:
        """Execute one authorization and append trusted command evidence."""
        record = self._store.load(authorization.task_id)
        snapshot = self._workspace_probe()
        _validate_workspace(record.current_envelope, snapshot)
        _validate_authorization(record, authorization)

        with WorkspaceLease(
            self._artifact_root / "leases",
            snapshot.worktree_path,
            authorization.task_id,
        ):
            record = self._store.load(authorization.task_id)
            locked_snapshot = self._workspace_probe()
            _validate_workspace(record.current_envelope, locked_snapshot)
            _validate_authorization(record, authorization)
            sandbox_enforced = record.current_envelope.integration_mode == "enforced"
            attempt = 1 + sum(
                event.type == "command.started"
                and event.data.get("capability_id") == authorization.capability_id
                for event in record.events
            )
            temporary_directory = Path(
                tempfile.mkdtemp(prefix="adaptive-harness-command-")
            )
            try:
                command = (
                    _sandbox_command(authorization, temporary_directory)
                    if sandbox_enforced
                    else authorization.argv
                )
                artifact_dir = _create_artifact_directory(
                    self._artifact_root,
                    authorization.task_id,
                    authorization.authorization_event_sequence,
                    attempt,
                )
                self._store.append_event(
                    authorization.task_id,
                    "command.started",
                    {
                        "authorization_event_sequence": (
                            authorization.authorization_event_sequence
                        ),
                        "capability_id": authorization.capability_id,
                        "attempt": attempt,
                    },
                )
                return self._run(
                    authorization,
                    attempt,
                    cancellation,
                    artifact_dir,
                    command,
                    sandbox_enforced,
                    temporary_directory,
                )
            finally:
                shutil.rmtree(temporary_directory, ignore_errors=True)

    def _run(
        self,
        authorization: AuthorizedCommand,
        attempt: int,
        cancellation: Event | None,
        artifact_dir: Path,
        command: tuple[str, ...],
        sandbox_enforced: bool,
        temporary_directory: Path,
    ) -> CommandResult:
        stdout_artifact = artifact_dir / "stdout.txt"
        stderr_artifact = artifact_dir / "stderr.txt"
        started_at = time.monotonic()

        try:
            process = subprocess.Popen(
                command,
                cwd=authorization.cwd,
                env=_controlled_environment(temporary_directory),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as error:
            message = self._redact(str(error))
            stdout_artifact.write_text("", encoding="utf-8")
            stderr_artifact.write_text(message + "\n", encoding="utf-8")
            return self._finish(
                authorization=authorization,
                attempt=attempt,
                status="environment_failure",
                exit_code=None,
                timed_out=False,
                cancelled=False,
                output_truncated=False,
                stdout_artifact=stdout_artifact,
                stderr_artifact=stderr_artifact,
                stdout_text="",
                stderr_text=message + "\n",
                sandbox_enforced=sandbox_enforced,
            )

        stdout, stderr, timed_out, cancelled, output_truncated = self._capture(
            process, authorization.timeout_seconds, cancellation, started_at
        )
        stdout_text, stderr_text, redaction_truncated = self._redact_and_limit(
            stdout, stderr
        )
        output_truncated = output_truncated or redaction_truncated
        stdout_artifact.write_text(stdout_text, encoding="utf-8")
        stderr_artifact.write_text(stderr_text, encoding="utf-8")

        if cancelled:
            status = "cancelled"
        elif timed_out:
            status = "timed_out"
        elif output_truncated:
            status = "output_limit_exceeded"
        elif process.returncode == 0:
            status = "succeeded"
        else:
            status = "command_failure"

        return self._finish(
            authorization=authorization,
            attempt=attempt,
            status=status,
            exit_code=process.returncode,
            timed_out=timed_out,
            cancelled=cancelled,
            output_truncated=output_truncated,
            stdout_artifact=stdout_artifact,
            stderr_artifact=stderr_artifact,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            sandbox_enforced=sandbox_enforced,
        )

    def _capture(
        self,
        process: subprocess.Popen[bytes],
        timeout_seconds: int,
        cancellation: Event | None,
        started_at: float,
    ) -> tuple[bytes, bytes, bool, bool, bool]:
        if process.stdout is None or process.stderr is None:
            raise ExecutorError("executor pipes were not created")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        timed_out = False
        cancelled = False
        output_truncated = False
        termination_requested = False

        while selector.get_map() or process.poll() is None:
            if (
                not termination_requested
                and cancellation is not None
                and cancellation.is_set()
            ):
                cancelled = True
                termination_requested = True
                _terminate_process(process, self._limits.termination_grace_seconds)
            if (
                not termination_requested
                and time.monotonic() - started_at >= timeout_seconds
            ):
                timed_out = True
                termination_requested = True
                _terminate_process(process, self._limits.termination_grace_seconds)

            for key, _ in selector.select(self._limits.poll_interval_seconds):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                current_size = sum(len(buffer) for buffer in buffers.values())
                remaining = self._limits.max_output_bytes - current_size
                if remaining > 0:
                    buffers[key.data].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    output_truncated = True
                    if not termination_requested:
                        termination_requested = True
                        _terminate_process(
                            process, self._limits.termination_grace_seconds
                        )

        process.wait()
        selector.close()
        return (
            bytes(buffers["stdout"]),
            bytes(buffers["stderr"]),
            timed_out,
            cancelled,
            output_truncated,
        )

    def _redact_and_limit(
        self, stdout: bytes, stderr: bytes
    ) -> tuple[str, str, bool]:
        stdout_text = self._redact(stdout.decode("utf-8", errors="replace"))
        stderr_text = self._redact(stderr.decode("utf-8", errors="replace"))
        combined = (stdout_text + stderr_text).encode("utf-8")
        if len(combined) <= self._limits.max_output_bytes:
            return stdout_text, stderr_text, False

        stdout_text = _limit_text(stdout_text, self._limits.max_output_bytes)
        remaining = self._limits.max_output_bytes - len(stdout_text.encode("utf-8"))
        stderr_text = _limit_text(stderr_text, remaining)
        return stdout_text, stderr_text, True

    def _redact(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def _finish(
        self,
        *,
        authorization: AuthorizedCommand,
        attempt: int,
        status: str,
        exit_code: int | None,
        timed_out: bool,
        cancelled: bool,
        output_truncated: bool,
        stdout_artifact: Path,
        stderr_artifact: Path,
        stdout_text: str,
        stderr_text: str,
        sandbox_enforced: bool,
    ) -> CommandResult:
        self._store.append_event(
            authorization.task_id,
            "command.finished",
            {
                "producer": "adaptive_harness.executor",
                "authorization_event_sequence": (
                    authorization.authorization_event_sequence
                ),
                "capability_id": authorization.capability_id,
                "attempt": attempt,
                "status": status,
                "exit_code": exit_code,
                "timed_out": timed_out,
                "cancelled": cancelled,
                "output_truncated": output_truncated,
                "sandbox_enforced": sandbox_enforced,
                "stdout_artifact": str(stdout_artifact),
                "stderr_artifact": str(stderr_artifact),
                "stdout_summary": stdout_text[: self._limits.summary_characters],
                "stderr_summary": stderr_text[: self._limits.summary_characters],
            },
        )
        return CommandResult(
            task_id=authorization.task_id,
            capability_id=authorization.capability_id,
            status=status,
            exit_code=exit_code,
            timed_out=timed_out,
            cancelled=cancelled,
            output_truncated=output_truncated,
            stdout_artifact=stdout_artifact,
            stderr_artifact=stderr_artifact,
            authorization_event_sequence=(
                authorization.authorization_event_sequence
            ),
            attempt=attempt,
        )


def _validate_workspace(
    envelope: TaskEnvelope, snapshot: WorkspaceSnapshot
) -> None:
    if (
        snapshot.repository_id != envelope.repository_id
        or snapshot.base_sha != envelope.base_sha
        or snapshot.worktree_path != envelope.worktree_path
    ):
        raise ExecutionRejectedError(
            "workspace identity changed after command authorization"
        )


def _validate_authorization(
    record: TaskRecord, authorization: AuthorizedCommand
) -> None:
    sequence = authorization.authorization_event_sequence
    if sequence < 1 or sequence > len(record.events):
        raise ExecutionRejectedError("authorization event does not exist")
    event = record.events[sequence - 1]
    if not _event_matches_authorization(event, authorization):
        raise ExecutionRejectedError("authorization event does not match command")
    if any(
        item.type == "command.started"
        and item.data.get("authorization_event_sequence") == sequence
        for item in record.events
    ):
        raise ExecutionRejectedError("authorization was already consumed")


def _event_matches_authorization(
    event: TaskEvent, authorization: AuthorizedCommand
) -> bool:
    return (
        event.type == "command.authorized"
        and event.data.get("capability_id") == authorization.capability_id
        and event.data.get("argv") == list(authorization.argv)
        and event.data.get("cwd") == str(authorization.cwd)
        and event.data.get("timeout_seconds") == authorization.timeout_seconds
        and event.data.get("approval_id") == authorization.approval_id
        and event.data.get("stop_on_exit_codes")
        == list(authorization.stop_on_exit_codes)
    )


def _sandbox_command(
    authorization: AuthorizedCommand, temporary_directory: Path
) -> tuple[str, ...]:
    """Wrap an authorized command in the host OS sandbox or fail closed."""
    if authorization.listener:
        raise ExecutionRejectedError(
            "enforced listener execution has no verified sandbox profile"
        )
    worktree = authorization.cwd
    while worktree.parent != worktree and not (worktree / ".git").exists():
        worktree = worktree.parent
    if not (worktree / ".git").exists():
        worktree = authorization.cwd
    git_metadata = worktree.resolve() / ".git"
    write_paths: list[Path] = []
    for value in authorization.write_paths:
        candidate = (worktree / value).resolve()
        if not candidate.is_relative_to(worktree.resolve()):
            raise ExecutionRejectedError(
                f"sandbox write path escapes the worktree: {value}"
            )
        if not candidate.exists():
            raise ExecutionRejectedError(
                f"sandbox write path must already exist: {value}"
            )
        if candidate == git_metadata or candidate.is_relative_to(git_metadata):
            raise ExecutionRejectedError(
                f"sandbox write path overlaps protected Git metadata: {value}"
            )
        write_paths.append(candidate)

    if sys.platform == "darwin":
        executable = shutil.which("sandbox-exec")
        if executable is None:
            raise ExecutionRejectedError("macOS sandbox-exec is unavailable")
        rules = [
            "(version 1)",
            "(deny default)",
            "(allow process*)",
            "(allow file-read*)",
            "(allow sysctl-read)",
            '(allow file-write-data (literal "/dev/null"))',
            "(allow file-write* (subpath "
            f'"{_sandbox_escape(str(temporary_directory))}"))',
        ]
        rules.extend(
            f'(allow file-write* (subpath "{_sandbox_escape(str(path))}"))'
            for path in write_paths
        )
        if git_metadata.exists():
            rules.append(
                f'(deny file-write* (literal "{_sandbox_escape(str(git_metadata))}"))'
            )
            rules.append(
                f'(deny file-write* (subpath "{_sandbox_escape(str(git_metadata))}"))'
            )
        if authorization.network is NetworkAccess.OUTBOUND:
            rules.append("(allow network-outbound)")
        else:
            rules.append("(deny network*)")
        return (executable, "-p", "".join(rules), *authorization.argv)

    if sys.platform.startswith("linux"):
        executable = shutil.which("bwrap")
        if executable is None:
            raise ExecutionRejectedError("Linux bubblewrap is unavailable")
        command: list[str] = [
            executable,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--bind",
            str(temporary_directory),
            str(temporary_directory),
            "--ro-bind",
            str(worktree.resolve()),
            str(worktree.resolve()),
        ]
        if authorization.network is NetworkAccess.OUTBOUND:
            command.remove("--unshare-all")
            command.extend(("--unshare-user", "--unshare-pid", "--unshare-uts"))
        for path in write_paths:
            command.extend(("--bind", str(path), str(path)))
        if git_metadata.exists():
            command.extend(
                ("--ro-bind", str(git_metadata), str(git_metadata))
            )
        command.extend(("--chdir", str(authorization.cwd), "--", *authorization.argv))
        return tuple(command)
    raise ExecutionRejectedError("no verified OS sandbox exists on this platform")


def _sandbox_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _controlled_environment(temporary_directory: Path) -> dict[str, str]:
    allowed_names = ("PATH", "LANG", "LC_ALL")
    environment = {
        name: os.environ[name] for name in allowed_names if name in os.environ
    }
    environment.setdefault("LANG", "C.UTF-8")
    environment["TMPDIR"] = str(temporary_directory)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _create_artifact_directory(
    artifact_root: Path,
    task_id: str,
    authorization_sequence: int,
    attempt: int,
) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    if artifact_root.is_symlink() or not artifact_root.is_dir():
        raise ExecutionRejectedError("artifact root is not a trusted directory")
    current = artifact_root
    for component in (task_id, "commands"):
        candidate = current / component
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            if candidate.is_symlink() or not candidate.is_dir():
                raise ExecutionRejectedError(
                    "artifact path contains a symlink or non-directory"
                ) from None
        current = candidate
    artifact_dir = current / f"{authorization_sequence}-{attempt}"
    try:
        artifact_dir.mkdir(mode=0o700)
    except FileExistsError as error:
        raise ExecutionRejectedError(
            "artifact directory already exists for this attempt"
        ) from error
    return artifact_dir


def _terminate_process(
    process: subprocess.Popen[bytes], grace_seconds: float
) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        process.wait()


def _limit_text(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")


__all__ = [
    "CommandResult",
    "ExecutionLimits",
    "ExecutionRejectedError",
    "Executor",
    "ExecutorError",
    "LeaseConflictError",
    "WorkspaceLease",
]
