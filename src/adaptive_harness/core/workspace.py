"""Deterministic Git workspace identity and diff facts."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from adaptive_harness.core.envelope import TaskEnvelope
from adaptive_harness.core.gateway import WorkspaceSnapshot

_FULL_GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class WorkspaceProbeError(RuntimeError):
    """Raised when authoritative Git workspace facts cannot be obtained."""


@dataclass(frozen=True, slots=True)
class WorkspaceFacts:
    """One deterministic observation of a local Git worktree."""

    snapshot: WorkspaceSnapshot
    dirty_state: tuple[str, ...]
    changed_paths: tuple[str, ...]


class GitWorkspace:
    """Read workspace identity without modifying repository state."""

    def __init__(
        self,
        path: Path,
        *,
        git_executable: str = "git",
        timeout_seconds: float = 2.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._path = Path(path).resolve()
        self._git_executable = git_executable
        self._timeout_seconds = timeout_seconds

    def snapshot(self) -> WorkspaceSnapshot:
        """Return canonical repository, HEAD, and worktree identity."""
        worktree = self._worktree_root()
        base_sha = self._base_sha()
        common_dir = self._common_git_dir(worktree)
        digest = hashlib.sha256(str(common_dir).encode("utf-8")).hexdigest()
        return WorkspaceSnapshot(
            repository_id=f"git-{digest}",
            base_sha=base_sha,
            worktree_path=worktree,
        )

    def inspect(self, *, base_sha: str | None = None) -> WorkspaceFacts:
        """Return identity, dirty status, and paths changed from a base commit."""
        snapshot = self.snapshot()
        comparison_base = base_sha or snapshot.base_sha
        return WorkspaceFacts(
            snapshot=snapshot,
            dirty_state=self.dirty_state(),
            changed_paths=self.changed_paths(comparison_base),
        )

    def dirty_state(self) -> tuple[str, ...]:
        """Return stable porcelain entries for the current dirty state."""
        output = self._run_text(
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            description="read dirty state",
        )
        return tuple(line for line in output.splitlines() if line)

    def changed_paths(self, base_sha: str) -> tuple[str, ...]:
        """Return tracked and untracked repository-relative paths."""
        if _FULL_GIT_SHA.fullmatch(base_sha) is None:
            raise WorkspaceProbeError("cannot compute diff from an invalid base SHA")
        tracked = self._run_bytes(
            "diff",
            "--name-only",
            "-z",
            base_sha,
            "--",
            description="compute tracked diff",
        )
        untracked = self._run_bytes(
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            description="compute untracked diff",
        )
        paths = {
            item
            for item in (*_split_nul(tracked), *_split_nul(untracked))
            if item
        }
        return tuple(sorted(paths))

    def diff_for(self, envelope: TaskEnvelope) -> tuple[str, ...]:
        """Validate task identity and return the diff bound to its base SHA."""
        snapshot = self.snapshot()
        if (
            snapshot.repository_id != envelope.repository_id
            or snapshot.worktree_path != envelope.worktree_path
        ):
            raise WorkspaceProbeError(
                "task envelope belongs to a different Git worktree"
            )
        return self.changed_paths(envelope.base_sha)

    def _worktree_root(self) -> Path:
        output = self._run_text(
            "rev-parse",
            "--show-toplevel",
            description="locate Git worktree",
            error_message="path is not inside a Git worktree",
        )
        return Path(output).resolve()

    def _base_sha(self) -> str:
        output = self._run_text(
            "rev-parse",
            "--verify",
            "HEAD^{commit}",
            description="resolve base commit",
            error_message="Git worktree has no base commit",
        )
        if _FULL_GIT_SHA.fullmatch(output) is None:
            raise WorkspaceProbeError("Git returned a non-canonical base commit")
        return output

    def _common_git_dir(self, worktree: Path) -> Path:
        output = self._run_text(
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
            description="resolve common Git directory",
        )
        path = Path(output)
        if not path.is_absolute():
            path = worktree / path
        return path.resolve()

    def _run_text(
        self,
        *args: str,
        description: str,
        error_message: str | None = None,
    ) -> str:
        return self._run(
            args, description=description, error_message=error_message
        ).stdout.decode("utf-8", errors="strict").strip()

    def _run_bytes(
        self,
        *args: str,
        description: str,
        error_message: str | None = None,
    ) -> bytes:
        return self._run(
            args, description=description, error_message=error_message
        ).stdout

    def _run(
        self,
        args: tuple[str, ...],
        *,
        description: str,
        error_message: str | None,
    ) -> subprocess.CompletedProcess[bytes]:
        environment = dict(os.environ)
        environment.update(
            {
                "GIT_OPTIONAL_LOCKS": "0",
                "LC_ALL": "C",
            }
        )
        try:
            result = subprocess.run(
                (
                    self._git_executable,
                    "-c",
                    "core.quotepath=false",
                    "-C",
                    str(self._path),
                    *args,
                ),
                check=False,
                capture_output=True,
                timeout=self._timeout_seconds,
                env=environment,
            )
        except FileNotFoundError as error:
            raise WorkspaceProbeError("Git executable was not found") from error
        except subprocess.TimeoutExpired as error:
            raise WorkspaceProbeError(
                f"Git timed out while trying to {description}"
            ) from error
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            message = error_message or f"Git failed to {description}"
            if stderr:
                message = f"{message}: {stderr}"
            raise WorkspaceProbeError(message)
        return result


def _split_nul(value: bytes) -> tuple[str, ...]:
    return tuple(
        item.decode("utf-8", errors="surrogateescape")
        for item in value.split(b"\0")
        if item
    )


__all__ = ["GitWorkspace", "WorkspaceFacts", "WorkspaceProbeError"]
