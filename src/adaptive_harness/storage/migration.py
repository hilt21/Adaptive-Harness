"""Reviewed, copy-first migration between local storage placements."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from adaptive_harness.core.store import TaskStore
from adaptive_harness.storage.location import (
    StorageLocator,
    StorageMode,
    project_data_lock,
)

_NON_MIGRATABLE_STATES = {"active", "blocked", "draft"}


@dataclass(frozen=True, slots=True)
class MigrationItem:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class StorageMigrationPlan:
    source_mode: StorageMode
    target_mode: StorageMode
    source: Path
    target: Path
    items: tuple[MigrationItem, ...]
    conflicts: tuple[str, ...] = ()
    reuse_target: bool = False

    @property
    def bytes_to_copy(self) -> int:
        return sum(item.size for item in self.items)


class StorageMigrator:
    """Copy and verify records before atomically changing the clone-local mode."""

    def __init__(self, locator: StorageLocator) -> None:
        self.locator = locator

    def plan(
        self, target_mode: StorageMode, *, rollback: bool = False
    ) -> StorageMigrationPlan:
        source_location = self.locator.location()
        if source_location.mode is target_mode:
            raise ValueError(f"storage mode is already {target_mode.value}")
        target_location = self.locator.location_for(target_mode)
        items = _manifest(source_location.project_data)
        _reject_unfinished_tasks(source_location.project_data)
        conflicts: tuple[str, ...] = ()
        if target_location.project_data.exists() and not rollback:
            target_items = _manifest(target_location.project_data)
            conflicts = tuple(item.path for item in target_items) or (".",)
        if rollback:
            if not target_location.project_data.exists() and items:
                raise ValueError("storage migration rollback target is unavailable")
            if _manifest(target_location.project_data) != items:
                raise ValueError(
                    "storage migration rollback target differs from current records"
                )
        return StorageMigrationPlan(
            source_location.mode,
            target_mode,
            source_location.project_data,
            target_location.project_data,
            items,
            conflicts,
            rollback,
        )

    def apply(self, plan: StorageMigrationPlan) -> None:
        with self.locator.operation_lock(), ExitStack() as locks:
            for root in sorted({plan.source, plan.target}, key=str):
                locks.enter_context(project_data_lock(root))
            self._apply_locked(plan)

    def _apply_locked(self, plan: StorageMigrationPlan) -> None:
        current = self.locator.location()
        expected_target = self.locator.location_for(plan.target_mode).project_data
        if current.mode is not plan.source_mode or current.project_data != plan.source:
            raise ValueError("storage mode changed after migration review")
        if expected_target != plan.target:
            raise ValueError("storage migration target changed after review")
        if plan.conflicts:
            raise ValueError(
                f"storage migration target has conflicts: {plan.target}"
            )
        if plan.reuse_target:
            if _manifest(plan.source) != plan.items:
                raise ValueError("storage source changed after migration review")
            if _manifest(plan.target) != plan.items:
                raise ValueError("storage rollback target changed after review")
            _reject_unfinished_tasks(plan.source)
            self.locator.set_mode(plan.target_mode)
            return
        if plan.target.exists():
            raise ValueError(f"storage migration target already exists: {plan.target}")
        if _manifest(plan.source) != plan.items:
            raise ValueError("storage source changed after migration review")
        _reject_unfinished_tasks(plan.source)

        plan.target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(
                dir=plan.target.parent,
                prefix=f".{plan.target.name}-migration-",
            )
        )
        installed_target = False
        try:
            _copy_items(plan.source, temporary, plan.items)
            if _manifest(temporary) != plan.items:
                raise ValueError("storage migration copy failed verification")
            os.replace(temporary, plan.target)
            installed_target = True
            self.locator.set_mode(plan.target_mode)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            if installed_target and self.locator.mode() is plan.source_mode:
                shutil.rmtree(plan.target)
            raise


def _manifest(root: Path) -> tuple[MigrationItem, ...]:
    if not root.exists():
        return ()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"storage root is not a regular directory: {root}")
    items: list[MigrationItem] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"storage migration refuses symlink: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"storage migration found unsupported entry: {path}")
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        items.append(
            MigrationItem(relative, len(content), hashlib.sha256(content).hexdigest())
        )
    return tuple(items)


def _reject_unfinished_tasks(root: Path) -> None:
    task_roots = [root] if (root / "tasks").is_dir() else []
    worktrees = root / "worktrees"
    if worktrees.is_dir():
        task_roots.extend(
            path for path in sorted(worktrees.iterdir()) if (path / "tasks").is_dir()
        )
    for task_root in task_roots:
        store = TaskStore(task_root)
        for record_path in sorted((task_root / "tasks").glob("*/record.json")):
            record = store.load(record_path.parent.name)
            if record.state in _NON_MIGRATABLE_STATES:
                raise ValueError(
                    f"cannot migrate storage while task {record.task_id} "
                    f"is {record.state}"
                )


def _copy_items(
    source: Path, target: Path, items: tuple[MigrationItem, ...]
) -> None:
    for item in items:
        relative = PurePosixPath(item.path)
        destination = target.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source.joinpath(*relative.parts), destination)


__all__ = ["MigrationItem", "StorageMigrationPlan", "StorageMigrator"]
