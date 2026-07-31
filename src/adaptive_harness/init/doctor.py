"""Read-only health checks for an initialized project."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from jsonschema import ValidationError

from adaptive_harness.adapters.managed import (
    ManagedProjection,
    ProjectionConflictError,
)
from adaptive_harness.core.workspace import GitWorkspace, WorkspaceProbeError
from adaptive_harness.init.transaction import InitializationError, RepositoryTransaction
from adaptive_harness.schemas import validator_for


@dataclass(frozen=True, slots=True)
class Diagnostic:
    name: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class DoctorReport:
    root: Path
    checks: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return all(check.status != "error" for check in self.checks)

    def check(self, name: str) -> Diagnostic:
        for diagnostic in self.checks:
            if diagnostic.name == name:
                return diagnostic
        raise KeyError(name)


class Doctor:
    """Validate schemas, projections, module locks, and workspace identity."""

    def __init__(
        self,
        root: Path,
        *,
        builtin_module_hashes: Mapping[str, str] | None = None,
    ) -> None:
        from adaptive_harness.modules.registry import ModuleRegistry

        self._root = Path(root).resolve()
        self._builtin_module_hashes = dict(
            builtin_module_hashes
            if builtin_module_hashes is not None
            else ModuleRegistry().hashes()
        )

    def run(self) -> DoctorReport:
        checks: list[Diagnostic] = []
        transaction = RepositoryTransaction(self._root)
        if transaction.pending:
            checks.append(
                Diagnostic(
                    "transaction",
                    "error",
                    "unfinished initialization transaction requires recovery",
                )
            )
        else:
            checks.append(Diagnostic("transaction", "pass", "no pending transaction"))

        config = self._check_document("config", "config", checks)
        self._check_document("capabilities", "capabilities", checks)
        modules = self._check_document("modules-lock", "modules-lock", checks)
        self._check_runtime_version(config, checks)
        self._check_launchers(config, checks)
        self._check_adapter(config, checks)
        self._check_projections(config, checks)
        self._check_module_hashes(modules, checks)
        self._check_workspace(checks)
        return DoctorReport(root=self._root, checks=tuple(checks))

    def _check_document(
        self,
        check_name: str,
        schema_name: str,
        checks: list[Diagnostic],
    ) -> dict[str, Any] | None:
        filename = {
            "config": "config.json",
            "capabilities": "capabilities.json",
            "modules-lock": "modules.lock.json",
        }[check_name]
        path = self._root / ".harness" / filename
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            checks.append(
                Diagnostic(check_name, "error", f"canonical file is missing: {path}")
            )
            return None
        except (OSError, json.JSONDecodeError) as error:
            checks.append(
                Diagnostic(
                    check_name, "error", f"canonical file is unreadable: {error}"
                )
            )
            return None
        if not isinstance(value, dict):
            checks.append(
                Diagnostic(check_name, "error", "canonical JSON root is not an object")
            )
            return None
        try:
            validator_for(schema_name).validate(value)
        except ValidationError as error:
            checks.append(
                Diagnostic(
                    check_name,
                    "error",
                    f"schema validation failed: {error.message}",
                )
            )
            return None
        checks.append(Diagnostic(check_name, "pass", "schema is valid"))
        return cast(dict[str, Any], value)

    def _check_runtime_version(
        self,
        config: dict[str, Any] | None,
        checks: list[Diagnostic],
    ) -> None:
        from adaptive_harness.upgrade import check_runtime_version

        if config is None:
            checks.append(
                Diagnostic("runtime-version", "error", "valid config is unavailable")
            )
            return
        try:
            status = check_runtime_version(cast(str, config["runtime_version"]))
        except InitializationError as error:
            checks.append(Diagnostic("runtime-version", "error", str(error)))
            return
        checks.append(
            Diagnostic(
                "runtime-version",
                "pass" if status.compatible else "error",
                status.message,
            )
        )

    def _check_launchers(
        self,
        config: dict[str, Any] | None,
        checks: list[Diagnostic],
    ) -> None:
        resolved = shutil.which("adp-harness")
        checks.append(
            Diagnostic(
                "terminal-command",
                "pass" if resolved is not None else "warning",
                (
                    f"terminal resolves adp-harness to {resolved}"
                    if resolved is not None
                    else "terminal PATH does not resolve adp-harness"
                ),
            )
        )
        if config is None:
            checks.append(
                Diagnostic(
                    "client-launcher",
                    "error",
                    "valid config is unavailable",
                )
            )
            return
        adapter = cast(dict[str, Any], config["adapter"])
        if adapter["id"] == "generic":
            checks.append(
                Diagnostic(
                    "client-launcher",
                    "pass",
                    "generic terminal integration has no managed client launcher",
                )
            )
            return
        launcher = Path.home() / ".local/bin/adp-harness"
        available = (
            not launcher.is_symlink()
            and launcher.is_file()
            and os.access(launcher, os.X_OK)
        )
        expected_version = cast(str, config["runtime_version"])
        working = False
        if available:
            try:
                completed = subprocess.run(
                    (str(launcher), "--version"),
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            else:
                working = (
                    completed.returncode == 0
                    and completed.stdout.strip() == expected_version
                )
        required = adapter["mode"] == "enforced"
        status = "pass" if working else ("error" if required else "warning")
        checks.append(
            Diagnostic(
                "client-launcher",
                status,
                (
                    f"managed client launcher is working at {launcher}"
                    if working
                    else (
                        f"managed client launcher is unavailable at {launcher}"
                        if not available
                        else (
                            "managed client launcher failed its version check "
                            f"at {launcher}"
                        )
                    )
                ),
            )
        )

    def _check_adapter(
        self,
        config: dict[str, Any] | None,
        checks: list[Diagnostic],
    ) -> None:
        if config is None:
            checks.append(Diagnostic("adapter", "error", "valid config is unavailable"))
            return
        adapter = cast(dict[str, Any], config["adapter"])
        if adapter["mode"] == "enforced":
            from adaptive_harness.adapters import (
                HealthState,
                IntegrationMode,
                adapter_for,
            )

            implementation = adapter_for(cast(str, adapter["id"]), self._root)
            probe = implementation.capability_probe()
            health = implementation.health_check()
            if (
                probe.mode is IntegrationMode.ENFORCED
                and probe.can_intercept
                and probe.verified_e2e
                and health.state is HealthState.HEALTHY
            ):
                checks.append(
                    Diagnostic(
                        "adapter",
                        "pass",
                        f"adapter {adapter['id']} enforcement is verified and healthy",
                    )
                )
                return
            checks.append(
                Diagnostic(
                    "adapter",
                    "error",
                    (
                        "enforced mode is not verified or is unhealthy for "
                        f"{adapter['id']}"
                    ),
                )
            )
            return
        checks.append(
            Diagnostic(
                "adapter",
                "pass",
                f"adapter {adapter['id']} is explicitly observe-only",
            )
        )

    def _check_projections(
        self,
        config: dict[str, Any] | None,
        checks: list[Diagnostic],
    ) -> None:
        if config is None:
            checks.append(
                Diagnostic(
                    "managed-projections", "error", "valid config is unavailable"
                )
            )
            return
        projection = ManagedProjection.agent_instructions()
        for item in cast(list[dict[str, Any]], config["managed_projections"]):
            relative = PurePosixPath(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                checks.append(
                    Diagnostic(
                        "managed-projections",
                        "error",
                        f"projection path is unsafe: {relative}",
                    )
                )
                return
            path = self._root.joinpath(*relative.parts)
            if path.is_symlink():
                checks.append(
                    Diagnostic(
                        "managed-projections",
                        "error",
                        f"projection path is a symlink: {relative}",
                    )
                )
                return
            try:
                content = path.read_text(encoding="utf-8")
                status = projection.inspect(content)
            except (OSError, UnicodeError, ProjectionConflictError) as error:
                checks.append(
                    Diagnostic(
                        "managed-projections",
                        "error",
                        f"projection cannot be verified: {error}",
                    )
                )
                return
            if (
                not status.valid
                or item["version"] != projection.version
                or item["content_sha256"] != projection.content_hash
            ):
                checks.append(
                    Diagnostic(
                        "managed-projections",
                        "error",
                        f"projection drift detected: {relative}",
                    )
                )
                return
        checks.append(
            Diagnostic(
                "managed-projections", "pass", "managed projections are current"
            )
        )

    def _check_module_hashes(
        self,
        modules: dict[str, Any] | None,
        checks: list[Diagnostic],
    ) -> None:
        from adaptive_harness.modules.registry import ModuleRegistry

        if modules is None:
            checks.append(
                Diagnostic("module-hashes", "error", "valid module lock is unavailable")
            )
            return
        items = [
            *cast(list[dict[str, Any]], modules["modules"]),
            *cast(list[dict[str, Any]], modules["templates"]),
        ]
        for item in items:
            if item["source"] == "local":
                location = item.get("location")
                if not isinstance(location, str):
                    checks.append(
                        Diagnostic(
                            "module-hashes",
                            "error",
                            f"local locked item has no location: {item['id']}",
                        )
                    )
                    return
                relative = PurePosixPath(location)
                if relative.is_absolute() or ".." in relative.parts:
                    checks.append(
                        Diagnostic(
                            "module-hashes",
                            "error",
                            f"local locked item path is unsafe: {item['id']}",
                        )
                    )
                    return
                try:
                    manifest = ModuleRegistry().load_local(
                        self._root.joinpath(*relative.parts)
                    )
                except ValueError as error:
                    checks.append(
                        Diagnostic("module-hashes", "error", str(error))
                    )
                    return
                if manifest.sha256 != item["sha256"]:
                    checks.append(
                        Diagnostic(
                            "module-hashes",
                            "error",
                            f"local item hash mismatched: {item['id']}",
                        )
                    )
                    return
                continue
            if item["source"] != "builtin":
                checks.append(
                    Diagnostic(
                        "module-hashes",
                        "error",
                        f"unsupported locked item source: {item['id']}",
                    )
                )
                return
            expected = self._builtin_module_hashes.get(item["id"])
            if expected is None or expected != item["sha256"]:
                checks.append(
                    Diagnostic(
                        "module-hashes",
                        "error",
                        f"builtin item hash is unavailable or mismatched: {item['id']}",
                    )
                )
                return
        checks.append(Diagnostic("module-hashes", "pass", "module hashes are valid"))

    def _check_workspace(self, checks: list[Diagnostic]) -> None:
        try:
            snapshot = GitWorkspace(self._root).snapshot()
        except WorkspaceProbeError as error:
            checks.append(Diagnostic("workspace", "error", str(error)))
            return
        checks.append(
            Diagnostic(
                "workspace",
                "pass",
                f"workspace {snapshot.repository_id} at {snapshot.base_sha}",
            )
        )


__all__ = ["Diagnostic", "Doctor", "DoctorReport"]
