"""Pure-content builtin template catalog and explicit rendering plans."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from adaptive_harness.init.transaction import (
    InitializationError,
    PlannedChange,
    RepositoryTransaction,
)


@dataclass(frozen=True, slots=True)
class ContentTemplate:
    id: str
    version: str
    content: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class TemplatePlan:
    root: Path
    template_id: str
    output: str
    changes: tuple[PlannedChange, ...]

    def diff(self) -> str:
        if not self.changes:
            return ""
        change = self.changes[0]
        before = (
            change.before.decode("utf-8").splitlines(keepends=True)
            if change.before is not None
            else []
        )
        after = change.after.decode("utf-8").splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                before,
                after,
                fromfile=(
                    f"a/{change.path}" if change.before is not None else "/dev/null"
                ),
                tofile=f"b/{change.path}",
            )
        )


_TEMPLATES = (
    ContentTemplate(
        "implementation-plan",
        "1.0.0",
        "# Implementation plan\n\n"
        "1. Goal and assumptions\n"
        "2. Changes\n"
        "3. Verification\n"
        "4. Rollback\n",
    ),
    ContentTemplate(
        "handoff",
        "1.0.0",
        "# Handoff\n\n## Outcome\n\n## Evidence\n\n## Remaining risks\n",
    ),
)


class TemplateCatalog:
    """Render inert content only after a caller reviews the resulting diff."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._transaction = RepositoryTransaction(self.root)

    def list(self) -> tuple[ContentTemplate, ...]:
        return _TEMPLATES

    def plan_render(self, template_id: str, output: str) -> TemplatePlan:
        template = self.get(template_id)
        relative = PurePosixPath(output)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("template output must be a safe repository-relative path")
        if relative.parts[0] == ".harness":
            raise ValueError("templates cannot write Harness canonical state")
        target = self.root.joinpath(*relative.parts)
        current = self.root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise InitializationError(
                    f"template output contains a symlink: {output}"
                )
        if target.exists() and not target.is_file():
            raise InitializationError(f"template output is not a file: {output}")
        before = target.read_bytes() if target.is_file() else None
        after = template.content.encode("utf-8")
        changes = () if before == after else (PlannedChange(output, before, after),)
        return TemplatePlan(self.root, template_id, output, changes)

    def apply(self, plan: TemplatePlan) -> None:
        if plan.root != self.root:
            raise ValueError("template plan belongs to another project")
        self._transaction.apply(plan.changes)

    def get(self, template_id: str) -> ContentTemplate:
        for template in _TEMPLATES:
            if template.id == template_id:
                return template
        raise ValueError(f"unknown template: {template_id}")


__all__ = ["ContentTemplate", "TemplateCatalog", "TemplatePlan"]
