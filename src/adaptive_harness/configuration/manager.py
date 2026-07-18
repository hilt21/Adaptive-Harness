"""Explain and safely rebuild canonical configuration projections."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jsonschema import ValidationError

from adaptive_harness.adapters.managed import ManagedProjection
from adaptive_harness.init.transaction import (
    InitializationError,
    PlannedChange,
    RepositoryTransaction,
)
from adaptive_harness.schemas import validator_for


@dataclass(frozen=True, slots=True)
class ConfigPlan:
    root: Path
    operation: str
    changes: tuple[PlannedChange, ...]

    def diff(self) -> str:
        sections: list[str] = []
        for change in self.changes:
            sections.extend(
                difflib.unified_diff(
                    change.before.decode("utf-8").splitlines(keepends=True)
                    if change.before is not None
                    else [],
                    change.after.decode("utf-8").splitlines(keepends=True),
                    fromfile=(
                        f"a/{change.path}"
                        if change.before is not None
                        else "/dev/null"
                    ),
                    tofile=f"b/{change.path}",
                )
            )
        return "".join(sections)


class ConfigurationManager:
    """Expose provenance and rebuild only derivable formatting/projections."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._transaction = RepositoryTransaction(self.root)

    def explain(self) -> dict[str, Any]:
        config = self._document("config.json", "config")
        capabilities = self._document("capabilities.json", "capabilities")
        modules = self._document("modules.lock.json", "modules-lock")
        return {
            "root": str(self.root),
            "canonical": {
                "config": ".harness/config.json",
                "capabilities": ".harness/capabilities.json",
                "modules": ".harness/modules.lock.json",
            },
            "adapter": config["adapter"],
            "project_profile": config["project_profile"],
            "model_profile": config["model_profile"],
            "policy": config["policy"],
            "modules_policy": config["modules"],
            "feedback": config["feedback"],
            "managed_projections": config["managed_projections"],
            "declared_capability_ids": [
                item["id"] for item in capabilities["capabilities"]
            ],
            "locked_module_ids": [item["id"] for item in modules["modules"]],
            "provenance": "canonical-local-json",
        }

    def plan(self) -> ConfigPlan:
        documents = {
            ".harness/config.json": self._document("config.json", "config"),
            ".harness/capabilities.json": self._document(
                "capabilities.json", "capabilities"
            ),
            ".harness/modules.lock.json": self._document(
                "modules.lock.json", "modules-lock"
            ),
        }
        candidates = {path: _json_bytes(value) for path, value in documents.items()}
        config = documents[".harness/config.json"]
        projection = ManagedProjection.agent_instructions()
        for item in config["managed_projections"]:
            path = cast(str, item["path"])
            target = self.root / path
            if target.is_symlink():
                raise InitializationError(f"refusing to manage symlink: {path}")
            existing = target.read_text(encoding="utf-8") if target.is_file() else None
            candidates[path] = projection.render(existing).encode("utf-8")
        changes: list[PlannedChange] = []
        for path, after in sorted(candidates.items()):
            target = self.root / path
            before = target.read_bytes() if target.is_file() else None
            if before != after:
                changes.append(PlannedChange(path, before, after))
        return ConfigPlan(self.root, "config-rebuild", tuple(changes))

    def apply(self, plan: ConfigPlan) -> None:
        if plan.root != self.root:
            raise ValueError("config plan belongs to another project")
        self._transaction.apply(plan.changes)

    def _document(self, filename: str, schema: str) -> dict[str, Any]:
        path = self.root / ".harness" / filename
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise InitializationError(
                f"canonical file is unreadable: {filename}"
            ) from error
        try:
            validator_for(schema).validate(value)
        except ValidationError as error:
            raise InitializationError(
                f"canonical file does not match schema: {filename}: {error.message}"
            ) from error
        if not isinstance(value, dict):
            raise InitializationError(f"canonical file root is invalid: {filename}")
        return cast(dict[str, Any], value)


def _json_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


__all__ = ["ConfigPlan", "ConfigurationManager"]
