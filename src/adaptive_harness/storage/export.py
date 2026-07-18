"""Reviewed, redacted local support export."""

from __future__ import annotations

import getpass
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SENSITIVE_KEYS = re.compile(
    r"(?:secret|token|password|credential|environment|env|remote|username)", re.I
)
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s\"']+/)*[^\s\"']*")


@dataclass(frozen=True, slots=True)
class ExportPlan:
    output: Path
    before: bytes | None
    content: bytes
    sources: tuple[str, ...]
    fields: tuple[str, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "output": str(self.output),
            "sources": list(self.sources),
            "fields": list(self.fields),
            "bytes": len(self.content),
            "redactions": [
                "secrets",
                "absolute_paths",
                "remote",
                "username",
                "environment",
            ],
        }


class ExportManager:
    """Export selected local facts without raw repositories or environment data."""

    def __init__(
        self,
        project_root: Path,
        local_project_root: Path,
        *,
        secrets: tuple[str, ...] = (),
    ) -> None:
        if any(not secret for secret in secrets):
            raise ValueError("export secrets must be non-empty")
        self.project_root = Path(project_root).resolve()
        self.local_project_root = Path(local_project_root).resolve()
        self.secrets = tuple(sorted(set(secrets), key=len, reverse=True))

    def plan(self, output: Path) -> ExportPlan:
        target = Path(output).resolve()
        if target.is_symlink():
            raise ValueError("export output cannot be a symlink")
        sources: list[str] = []
        documents: dict[str, Any] = {}
        for name in ("config.json", "capabilities.json", "modules.lock.json"):
            path = self.project_root / ".harness" / name
            if path.is_file() and not path.is_symlink():
                documents[f"canonical/{name}"] = _load_json(path)
                sources.append(f".harness/{name}")
        if self.local_project_root.is_dir():
            for path in sorted(self.local_project_root.rglob("*.json")):
                if path.is_symlink() or path.resolve() == target:
                    continue
                relative = str(path.relative_to(self.local_project_root))
                try:
                    documents[f"local/{relative}"] = _load_json(path)
                except ValueError:
                    continue
                sources.append(f"local/{relative}")
        redacted = _redact(documents, self.secrets, getpass.getuser())
        export = {
            "schema_version": "1.0",
            "telemetry": "disabled",
            "sources": sources,
            "data": redacted,
        }
        content = (
            json.dumps(export, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        before = target.read_bytes() if target.is_file() else None
        return ExportPlan(
            target,
            before,
            content,
            tuple(sources),
            tuple(export),
        )

    def apply(self, plan: ExportPlan) -> None:
        if plan.output.is_symlink():
            raise ValueError("export output became a symlink")
        current = plan.output.read_bytes() if plan.output.is_file() else None
        if current != plan.before:
            raise ValueError("export output changed after review")
        plan.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=plan.output.parent,
            prefix=f".{plan.output.name}-",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(plan.content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, plan.output)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot export invalid JSON: {path.name}") from error


def _redact(value: Any, secrets: tuple[str, ...], username: str) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if _SENSITIVE_KEYS.search(key)
                else _redact(item, secrets, username)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, secrets, username) for item in value]
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED]")
        if username:
            redacted = redacted.replace(username, "<USER>")
        return _ABSOLUTE_PATH.sub("<ABSOLUTE_PATH>", redacted)
    return value


__all__ = ["ExportManager", "ExportPlan"]
