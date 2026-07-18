"""Recoverable multi-file repository transactions."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


class InitializationError(RuntimeError):
    """Raised when initialization cannot be safely applied."""


class PlanDriftError(InitializationError):
    """Raised when files changed after the user reviewed the plan."""


@dataclass(frozen=True, slots=True)
class PlannedChange:
    """One reviewed before/after file change."""

    path: str
    before: bytes | None
    after: bytes

    def __post_init__(self) -> None:
        _validate_relative_path(self.path)
        if self.before == self.after:
            raise ValueError("planned change must alter file content")


Committer = Callable[[Path, Path], None]


class RepositoryTransaction:
    """Stage, journal, commit, and recover a reviewed file set."""

    def __init__(
        self,
        root: Path,
        *,
        committer: Committer | None = None,
    ) -> None:
        self._root = Path(root).resolve()
        self._harness_dir = self._root / ".harness"
        self._journal_path = self._harness_dir / ".transaction.json"
        self._committer = committer or os.replace

    @property
    def pending(self) -> bool:
        return self._journal_path.is_file()

    def apply(self, changes: tuple[PlannedChange, ...]) -> None:
        if not changes:
            return
        if self.pending:
            raise InitializationError(
                "an unfinished initialization transaction must be recovered first"
            )
        self._validate_reviewed_state(changes)
        harness_dir_existed = self._harness_dir.exists()
        transaction_id = uuid.uuid4().hex
        staging_dir = self._harness_dir / ".staging" / transaction_id
        journal = {
            "schema_version": "1.0",
            "transaction_id": transaction_id,
            "harness_dir_existed": harness_dir_existed,
            "status": "preparing",
            "changes": [_journal_change(item) for item in changes],
        }
        self._harness_dir.mkdir(parents=True, exist_ok=True)
        self._write_journal(journal)
        try:
            for change in changes:
                staged = staging_dir.joinpath(*PurePosixPath(change.path).parts)
                staged.parent.mkdir(parents=True, exist_ok=True)
                _write_synced(staged, change.after)
            journal["status"] = "staged"
            self._write_journal(journal)
            for change in changes:
                staged = staging_dir.joinpath(*PurePosixPath(change.path).parts)
                target = self._target(change.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                self._committer(staged, target)
                _sync_directory(target.parent)
            self._cleanup(journal)
        except Exception as error:
            self._restore(journal)
            raise InitializationError(
                f"initialization transaction failed: {error}"
            ) from error

    def recover(self) -> bool:
        if not self.pending:
            return False
        try:
            value = json.loads(self._journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise InitializationError(
                "initialization transaction journal is unreadable"
            ) from error
        if not isinstance(value, dict):
            raise InitializationError(
                "initialization transaction journal has an invalid root"
            )
        self._restore(value)
        return True

    def _validate_reviewed_state(
        self, changes: tuple[PlannedChange, ...]
    ) -> None:
        seen: set[str] = set()
        for change in changes:
            if change.path in seen:
                raise InitializationError(
                    f"plan contains duplicate path: {change.path}"
                )
            seen.add(change.path)
            target = self._target(change.path)
            if target.is_symlink():
                raise InitializationError(
                    f"refusing to replace symlink: {change.path}"
                )
            current = target.read_bytes() if target.is_file() else None
            if current != change.before:
                raise PlanDriftError(
                    f"file changed after review: {change.path}"
                )

    def _restore(self, journal: dict[str, Any]) -> None:
        changes = journal.get("changes")
        if not isinstance(changes, list):
            raise InitializationError(
                "initialization transaction journal has invalid changes"
            )
        for item in changes:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise InitializationError(
                    "initialization transaction journal has an invalid change"
                )
            path = item["path"]
            target = self._target(path)
            before = item.get("before")
            if before is None:
                target.unlink(missing_ok=True)
            elif isinstance(before, str):
                try:
                    content = base64.b64decode(before, validate=True)
                except ValueError as error:
                    raise InitializationError(
                        "transaction backup is invalid"
                    ) from error
                target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(target, content)
            else:
                raise InitializationError(
                    "initialization transaction backup has invalid type"
                )
        self._cleanup(journal)

    def _cleanup(self, journal: dict[str, Any]) -> None:
        transaction_id = journal.get("transaction_id")
        if isinstance(transaction_id, str):
            staging_dir = self._harness_dir / ".staging" / transaction_id
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_root = self._harness_dir / ".staging"
        with suppress(OSError):
            staging_root.rmdir()
        self._journal_path.unlink(missing_ok=True)
        if journal.get("harness_dir_existed") is False:
            with suppress(OSError):
                self._harness_dir.rmdir()

    def _write_journal(self, journal: dict[str, Any]) -> None:
        serialized = (
            json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        _atomic_write(self._journal_path, serialized)

    def _target(self, relative_path: str) -> Path:
        _validate_relative_path(relative_path)
        relative = PurePosixPath(relative_path)
        target = self._root.joinpath(*relative.parts)
        current = self._root
        for component in relative.parts[:-1]:
            current /= component
            if current.is_symlink():
                raise InitializationError(
                    f"path contains a symlink: {relative_path}"
                )
        return target


def _journal_change(change: PlannedChange) -> dict[str, Any]:
    return {
        "path": change.path,
        "before": (
            base64.b64encode(change.before).decode("ascii")
            if change.before is not None
            else None
        ),
        "after_sha256": hashlib.sha256(change.after).hexdigest(),
    }


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or path.parts in {(), (".",)}
    ):
        raise ValueError("path must be repository-relative without '..'")


def _write_synced(path: Path, content: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "Committer",
    "InitializationError",
    "PlanDriftError",
    "PlannedChange",
    "RepositoryTransaction",
]
