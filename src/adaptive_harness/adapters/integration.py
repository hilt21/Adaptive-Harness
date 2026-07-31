"""Transactional lifecycle management for client integrations."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

from adaptive_harness.adapters.claude_hook import (
    install_settings,
    uninstall_settings,
)
from adaptive_harness.adapters.clients import ADAPTER_TYPES, adapter_for
from adaptive_harness.adapters.managed import ManagedProjection
from adaptive_harness.init.transaction import (
    InitializationError,
    PlannedChange,
    RepositoryTransaction,
)
from adaptive_harness.schemas import validator_for


@dataclass(frozen=True, slots=True)
class IntegrationPlan:
    root: Path
    adapter: str
    operation: str
    changes: tuple[PlannedChange, ...]

    def diff(self) -> str:
        sections: list[str] = []
        for change in self.changes:
            before = (
                change.before.decode("utf-8").splitlines(keepends=True)
                if change.before is not None
                else []
            )
            after = change.after.decode("utf-8").splitlines(keepends=True)
            sections.extend(
                difflib.unified_diff(
                    before,
                    after,
                    fromfile=(
                        f"a/{change.path}"
                        if change.before is not None
                        else "/dev/null"
                    ),
                    tofile=f"b/{change.path}",
                )
            )
        return "".join(sections)


class IntegrationManager:
    """Plan and atomically apply adapter projection changes."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._transaction = RepositoryTransaction(self.root)

    def plan_install(self, adapter_id: str) -> IntegrationPlan:
        return self._plan(adapter_id, "install")

    def plan_repair(self, adapter_id: str) -> IntegrationPlan:
        return self._plan(adapter_id, "repair")

    def plan_uninstall(self, adapter_id: str) -> IntegrationPlan:
        return self._plan(adapter_id, "uninstall")

    def apply(self, plan: IntegrationPlan) -> None:
        if plan.root != self.root:
            raise ValueError("integration plan belongs to another project")
        self._transaction.apply(plan.changes)

    def _plan(self, adapter_id: str, operation: str) -> IntegrationPlan:
        if adapter_id not in ADAPTER_TYPES:
            raise ValueError(f"unsupported adapter: {adapter_id}")
        config_path = self.root / ".harness/config.json"
        if not config_path.is_file():
            raise InitializationError(
                "run `adp-harness init` before managing integrations"
            )
        config = _load_config(config_path)
        candidates: dict[str, bytes] = {}
        projection = ManagedProjection.agent_instructions()

        if operation in {"install", "repair"}:
            if config["adapter"]["id"] == "claude-code" and adapter_id != "claude-code":
                existing_settings = self._read_optional(".claude/settings.json")
                removed_settings = uninstall_settings(existing_settings)
                if removed_settings is not None:
                    candidates[".claude/settings.json"] = removed_settings.encode(
                        "utf-8"
                    )
            for item in config["managed_projections"]:
                path = item["path"]
                if path != ADAPTER_TYPES[adapter_id].projection_path:
                    removed = self._removed_projection(path)
                    if removed is not None:
                        candidates[path] = removed
            adapter = adapter_for(adapter_id, self.root)
            if adapter.projection_path is not None:
                existing = self._read_optional(adapter.projection_path)
                rendered = adapter.install_integration(existing)
                if rendered is None:
                    raise AssertionError("projection adapter returned no content")
                candidates[adapter.projection_path] = rendered.encode("utf-8")
                managed = [
                    {
                        "path": adapter.projection_path,
                        "version": projection.version,
                        "content_sha256": projection.content_hash,
                    }
                ]
            else:
                managed = []
            if adapter_id == "claude-code":
                settings = self._read_optional(".claude/settings.json")
                candidates[".claude/settings.json"] = install_settings(
                    settings
                ).encode("utf-8")
            config["adapter"] = {
                "id": adapter_id,
                "mode": adapter.capability_probe().mode.value,
            }
            config["managed_projections"] = managed
        else:
            adapter = adapter_for(adapter_id, self.root)
            if adapter.projection_path is not None:
                removed = self._removed_projection(adapter.projection_path)
                if removed is not None:
                    candidates[adapter.projection_path] = removed
            if adapter_id == "claude-code":
                settings = self._read_optional(".claude/settings.json")
                removed_settings = uninstall_settings(settings)
                if removed_settings is not None:
                    candidates[".claude/settings.json"] = removed_settings.encode(
                        "utf-8"
                    )
            if config["adapter"]["id"] == adapter_id:
                config["adapter"] = {"id": "generic", "mode": "observe"}
                config["managed_projections"] = []

        candidates[".harness/config.json"] = _json_bytes(config)
        changes = tuple(
            change
            for path in sorted(candidates)
            if (change := self._change(path, candidates[path])) is not None
        )
        return IntegrationPlan(self.root, adapter_id, operation, changes)

    def _removed_projection(self, path: str) -> bytes | None:
        existing = self._read_optional(path)
        if existing is None:
            return None
        return ManagedProjection.agent_instructions().remove(existing).encode("utf-8")

    def _read_optional(self, path: str) -> str | None:
        target = self.root / path
        if target.is_symlink():
            raise InitializationError(f"refusing to manage symlink: {path}")
        if not target.exists():
            return None
        if not target.is_file():
            raise InitializationError(f"managed projection is not a file: {path}")
        return target.read_text(encoding="utf-8")

    def _change(self, path: str, after: bytes) -> PlannedChange | None:
        target = self.root / path
        if target.is_symlink():
            raise InitializationError(f"refusing to manage symlink: {path}")
        before = target.read_bytes() if target.is_file() else None
        if before == after:
            return None
        return PlannedChange(path, before, after)


def _load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InitializationError(f"Harness config is unreadable: {error}") from error
    try:
        validator_for("config").validate(value)
    except ValidationError as error:
        raise InitializationError(
            f"Harness config does not match its schema: {error.message}"
        ) from error
    if not isinstance(value, dict):
        raise InitializationError("Harness config must be an object")
    return value


def _json_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


__all__ = ["IntegrationManager", "IntegrationPlan"]
