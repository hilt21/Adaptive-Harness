"""Resolve per-clone local data placement without changing canonical config."""

from __future__ import annotations

import fcntl
import hashlib
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from adaptive_harness.core.workspace import GitWorkspace

_GIT_MODE_KEY = "harness.storageMode"


class StorageMode(StrEnum):
    USER_DATA = "user-data"
    REPOSITORY_LOCAL = "repository-local"


@dataclass(frozen=True, slots=True)
class StorageLocation:
    mode: StorageMode
    project_data: Path
    task_data: Path
    legacy_task_data: Path | None = None


class StorageLocator:
    """Map one Git clone to its selected local project-data directory."""

    def __init__(self, root: Path, user_data_root: Path) -> None:
        self.root = Path(root).resolve()
        self.user_data_root = Path(user_data_root).resolve()
        self.snapshot = GitWorkspace(self.root).snapshot()

    def mode(self) -> StorageMode:
        completed = self._git("config", "--local", "--get", _GIT_MODE_KEY)
        if completed.returncode == 1:
            return StorageMode.USER_DATA
        if completed.returncode != 0:
            raise ValueError("cannot read the local Harness storage mode")
        value = completed.stdout.strip()
        try:
            return StorageMode(value)
        except ValueError as error:
            raise ValueError(
                f"unsupported local Harness storage mode: {value}"
            ) from error

    def location(self, *, force_user_data: bool = False) -> StorageLocation:
        mode = StorageMode.USER_DATA if force_user_data else self.mode()
        return self.location_for(mode)

    def location_for(self, mode: StorageMode) -> StorageLocation:
        if mode is StorageMode.USER_DATA:
            project_data = (
                self.user_data_root / "projects" / self.snapshot.repository_id
            )
        else:
            project_data = self._git_common_dir() / "adaptive-harness"
        resolved_project_data = project_data.resolve()
        worktree_digest = hashlib.sha256(
            str(self.snapshot.worktree_path).encode("utf-8")
        ).hexdigest()
        task_data = resolved_project_data / "worktrees" / worktree_digest
        legacy_task_data = (
            resolved_project_data
            if (resolved_project_data / "tasks").is_dir()
            else None
        )
        return StorageLocation(
            mode, resolved_project_data, task_data, legacy_task_data
        )

    @contextmanager
    def operation_lock(self) -> Iterator[None]:
        """Serialize task writes and storage placement changes for one clone."""
        lock_path = self._git_common_dir() / "adaptive-harness.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def set_mode(self, mode: StorageMode) -> None:
        completed = self._git(
            "config", "--local", "--replace-all", _GIT_MODE_KEY, mode.value
        )
        if completed.returncode != 0:
            raise ValueError("cannot update the local Harness storage mode")

    def _git_common_dir(self) -> Path:
        completed = self._git(
            "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise ValueError("cannot resolve the Git common directory")
        common_dir = Path(completed.stdout.strip())
        if not common_dir.is_absolute():
            common_dir = self.root / common_dir
        return common_dir.resolve()

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("git", "-C", str(self.root), *arguments),
            capture_output=True,
            text=True,
            check=False,
        )


def resolve_project_data(
    data_root: Path | None,
    repository_id: str | None,
    project_data: Path | None,
) -> Path:
    """Resolve a legacy data-root pair or an explicit project-data path."""
    if project_data is not None:
        return Path(project_data).resolve()
    if (
        data_root is None
        or not repository_id
        or "/" in repository_id
        or ".." in repository_id
    ):
        raise ValueError("repository id is unsafe")
    return Path(data_root).resolve() / "projects" / repository_id


@contextmanager
def project_data_lock(project_data: Path) -> Iterator[None]:
    """Serialize writers with storage migration for one project-data root."""
    root = Path(project_data).resolve()
    root.parent.mkdir(parents=True, exist_ok=True)
    lock_path = root.parent / f".{root.name}.write.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def bound_project_data_lock(
    project_data: Path,
    storage_locator: StorageLocator | None,
    *,
    force_user_data: bool = False,
) -> Iterator[None]:
    """Lock a writer and reject a clone binding invalidated by migration."""
    root = Path(project_data).resolve()
    if storage_locator is None:
        raise ValueError("storage locator is required for project-data writes")
    with storage_locator.operation_lock():
        current = storage_locator.location(force_user_data=force_user_data)
        if current.project_data != root:
            raise ValueError(
                "storage placement changed; recreate storage writer"
            )
        with project_data_lock(root):
            yield


__all__ = [
    "StorageLocation",
    "StorageLocator",
    "StorageMode",
    "bound_project_data_lock",
    "project_data_lock",
    "resolve_project_data",
]
