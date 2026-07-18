"""Offline registry for builtin and explicitly supplied local modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from adaptive_harness.modules.model import ModuleManifest

_BUILTIN_DOCUMENTS: tuple[dict[str, Any], ...] = (
    {
        "schema_version": "1.0",
        "id": "tdd-guidance",
        "version": "1.0.0",
        "category": "workflow",
        "type": "declarative",
        "applies_when": ["code-change"],
        "excludes_when": ["documentation-only"],
        "required_capabilities": [],
        "cost": {"context_tokens": 180, "runtime_seconds": 0},
        "activation_policy": "suggest",
        "success_metrics": ["required-checks-pass"],
        "compatibility": {"runtime_major": 0, "protocol": "1.0"},
        "rollback": "disable the module and remove its task context",
        "content": "Write a failing test, implement the smallest fix, then verify it.",
    },
    {
        "schema_version": "1.0",
        "id": "task-summary",
        "version": "1.0.0",
        "category": "reporting",
        "type": "executable",
        "applies_when": ["task-complete"],
        "excludes_when": [],
        "required_capabilities": ["module-execution"],
        "cost": {"context_tokens": 0, "runtime_seconds": 2},
        "activation_policy": "manual",
        "success_metrics": ["valid-protocol-response"],
        "compatibility": {"runtime_major": 0, "protocol": "1.0"},
        "rollback": "disable the module and discard its generated summary",
        "entrypoint": ["builtin:task_summary"],
    },
)


class ModuleRegistry:
    """Discover trusted builtins and user-selected local manifests."""

    def __init__(self) -> None:
        self._builtins = tuple(
            ModuleManifest.from_dict(document) for document in _BUILTIN_DOCUMENTS
        )

    def list_builtin(self) -> tuple[ModuleManifest, ...]:
        return self._builtins

    def get_builtin(self, module_id: str) -> ModuleManifest:
        for manifest in self._builtins:
            if manifest.id == module_id:
                return manifest
        raise KeyError(module_id)

    def load_local(self, manifest_path: Path) -> ModuleManifest:
        path = Path(manifest_path).resolve()
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"local module manifest is not a regular file: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"local module manifest is unreadable: {error}") from error
        if not isinstance(value, dict):
            raise ValueError("local module manifest root must be an object")
        manifest = ModuleManifest.from_dict(cast(dict[str, Any], value))
        manifest.ensure_compatible()
        return manifest

    def hashes(self) -> dict[str, str]:
        return {manifest.id: manifest.sha256 for manifest in self._builtins}


__all__ = ["ModuleRegistry"]
