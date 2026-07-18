"""Fail-closed isolated subprocess runner for executable modules."""

from __future__ import annotations

import json
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from adaptive_harness.modules.model import ModuleManifest, ModuleType

_OUTPUT_LIMIT = 65_536
_ARTIFACT_LIMIT = 1_048_576


class ModuleExecutionError(RuntimeError):
    """Raised when a module cannot be executed under the required policy."""


@dataclass(frozen=True, slots=True)
class ModuleRunResult:
    status: str
    summary: str
    next_actions: tuple[str, ...]
    artifacts: tuple[str, ...]
    sandbox_enforced: bool


class ModuleRunner:
    """Launch modules with no shell, a clean environment, and an OS sandbox."""

    def __init__(self, *, output_limit: int = _OUTPUT_LIMIT) -> None:
        if output_limit < 1024:
            raise ValueError("module output limit must be at least 1024 bytes")
        self.output_limit = output_limit

    @property
    def sandbox_available(self) -> bool:
        system = platform.system()
        return (system == "Darwin" and shutil.which("sandbox-exec") is not None) or (
            system == "Linux" and shutil.which("bwrap") is not None
        )

    def run(
        self,
        manifest: ModuleManifest,
        *,
        task: dict[str, Any],
        available_capabilities: frozenset[str],
        artifact_root: Path,
        local_source_root: Path | None = None,
    ) -> ModuleRunResult:
        if manifest.module_type is not ModuleType.EXECUTABLE:
            raise ModuleExecutionError("declarative modules cannot be executed")
        manifest.ensure_compatible()
        missing = set(manifest.required_capabilities) - available_capabilities
        if missing:
            raise ModuleExecutionError(
                f"module capabilities are not approved: {', '.join(sorted(missing))}"
            )
        if not self.sandbox_available:
            raise ModuleExecutionError("no supported OS sandbox is available")
        command = self._entrypoint(manifest, local_source_root)
        timeout = max(1.0, manifest.cost.runtime_seconds)
        request = json.dumps(
            {
                "protocol_version": "1.0",
                "module_id": manifest.id,
                "task": task,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="harness-module-") as temporary:
            workdir = Path(temporary).resolve()
            sandboxed = self._sandbox_command(command, workdir)
            stdout_path = workdir / "stdout.json"
            stderr_path = workdir / "stderr.txt"
            try:
                with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                    completed = subprocess.run(
                        sandboxed,
                        input=request,
                        stdout=stdout,
                        stderr=stderr,
                        cwd=workdir,
                        env={},
                        timeout=timeout,
                        check=False,
                        shell=False,
                        preexec_fn=self._resource_limits,
                    )
            except subprocess.TimeoutExpired as error:
                raise ModuleExecutionError("module execution timed out") from error
            if completed.returncode != 0:
                raise ModuleExecutionError(
                    f"sandboxed module exited with code {completed.returncode}"
                )
            if stdout_path.stat().st_size > self.output_limit:
                raise ModuleExecutionError("module output exceeded its limit")
            try:
                response = json.loads(stdout_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ModuleExecutionError("module returned invalid JSON") from error
            result = self._validate_response(response)
            artifacts = self._collect_artifacts(
                workdir,
                cast(list[str], response["artifacts"]),
                Path(artifact_root),
                manifest.id,
            )
            return ModuleRunResult(
                result.status,
                result.summary,
                result.next_actions,
                artifacts,
                sandbox_enforced=True,
            )

    def _entrypoint(
        self, manifest: ModuleManifest, local_source_root: Path | None
    ) -> tuple[str, ...]:
        if manifest.entrypoint == ("builtin:task_summary",):
            script = Path(__file__).with_name("runners") / "task_summary.py"
            return (sys.executable, "-I", str(script))
        if local_source_root is None or len(manifest.entrypoint) != 1:
            raise ModuleExecutionError("local executable requires one relative script")
        relative = PurePosixPath(manifest.entrypoint[0])
        if relative.is_absolute() or ".." in relative.parts:
            raise ModuleExecutionError("local module entrypoint is unsafe")
        root = Path(local_source_root).resolve()
        script = root.joinpath(*relative.parts)
        if not script.is_file() or script.is_symlink():
            raise ModuleExecutionError("local module entrypoint is not a regular file")
        return (sys.executable, "-I", str(script))

    def _sandbox_command(
        self, command: tuple[str, ...], workdir: Path
    ) -> tuple[str, ...]:
        if platform.system() == "Darwin":
            executable = shutil.which("sandbox-exec")
            if executable is None:
                raise ModuleExecutionError("sandbox-exec is unavailable")
            escaped = str(workdir).replace('"', '\\"')
            profile = (
                "(version 1)(deny default)(allow process*)"
                "(allow file-read*)"
                "(allow sysctl-read)"
                '(allow file-write-data (literal "/dev/null"))'
                f'(allow file-write* (subpath "{escaped}"))'
                "(deny network*)"
            )
            return (executable, "-p", profile, *command)
        executable = shutil.which("bwrap")
        if executable is None:
            raise ModuleExecutionError("bubblewrap is unavailable")
        return (
            executable,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--ro-bind",
            "/",
            "/",
            "--bind",
            str(workdir),
            str(workdir),
            "--chdir",
            str(workdir),
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--",
            *command,
        )

    def _resource_limits(self) -> None:
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (self.output_limit, self.output_limit)
        )
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    def _validate_response(self, value: object) -> ModuleRunResult:
        if not isinstance(value, dict):
            raise ModuleExecutionError("module response root must be an object")
        status = value.get("status")
        summary = value.get("summary")
        next_actions = value.get("next_actions")
        artifacts = value.get("artifacts")
        if status not in {"success", "failed", "blocked"}:
            raise ModuleExecutionError("module response has invalid status")
        if not isinstance(summary, str):
            raise ModuleExecutionError("module response has invalid summary")
        if not _string_list(next_actions) or not _string_list(artifacts):
            raise ModuleExecutionError("module response lists are invalid")
        return ModuleRunResult(
            status,
            summary,
            tuple(cast(list[str], next_actions)),
            tuple(cast(list[str], artifacts)),
            sandbox_enforced=True,
        )

    def _collect_artifacts(
        self,
        workdir: Path,
        artifacts: list[str],
        artifact_root: Path,
        module_id: str,
    ) -> tuple[str, ...]:
        if len(artifacts) > 10:
            raise ModuleExecutionError("module returned too many artifacts")
        collected: list[str] = []
        destination_root = artifact_root.resolve() / module_id
        for item in artifacts:
            relative = PurePosixPath(item)
            if relative.is_absolute() or ".." in relative.parts:
                raise ModuleExecutionError("module artifact path is unsafe")
            source = workdir.joinpath(*relative.parts)
            if not source.is_file() or source.is_symlink():
                raise ModuleExecutionError("module artifact is not a regular file")
            if source.stat().st_size > _ARTIFACT_LIMIT:
                raise ModuleExecutionError("module artifact exceeded its limit")
            destination = destination_root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            collected.append(str(destination))
        return tuple(collected)


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


__all__ = ["ModuleExecutionError", "ModuleRunResult", "ModuleRunner"]
