"""Indexed local retention, pinning, and non-destructive cleanup planning."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, cast

_RETENTION_DAYS = {
    "record": 30,
    "artifact": 7,
    "approval": 90,
    "accepted_with_risk": 90,
    "minimal_episode": 30,
    "research_episode": 180,
}
_PROTECTED_TASK_STATES = {"active", "blocked", "waiting_approval"}


@dataclass(frozen=True, slots=True)
class StorageItem:
    id: str
    category: str
    path: str
    created_at: str
    task_state: str
    pinned: bool = False
    recommendation_ref: str | None = None
    expired: bool = False

    def __post_init__(self) -> None:
        _safe_relative(self.path)
        if self.category not in _RETENTION_DAYS:
            raise ValueError(f"unsupported storage category: {self.category}")


@dataclass(frozen=True, slots=True)
class StorageStatus:
    item_count: int
    active_count: int
    pinned_count: int
    expired_count: int
    bytes_on_disk: int


@dataclass(frozen=True, slots=True)
class PrunePlan:
    root: Path
    generated_at: str
    item_ids: tuple[str, ...]
    paths: tuple[str, ...]
    bytes_reclaimable: int


@dataclass(frozen=True, slots=True)
class PinPlan:
    root: Path
    item_id: str
    before: bytes
    after: bytes


class StorageManager:
    """Keep task-local data isolated by repository identity."""

    def __init__(self, data_root: Path, repository_id: str) -> None:
        if not repository_id or "/" in repository_id or ".." in repository_id:
            raise ValueError("repository id is unsafe")
        self.root = Path(data_root).resolve() / "projects" / repository_id
        self._index = self.root / "storage-index.json"

    def register(self, item: StorageItem) -> None:
        document = self._load()
        items = cast(list[dict[str, Any]], document["items"])
        items[:] = [existing for existing in items if existing["id"] != item.id]
        items.append(_item_document(item))
        items.sort(key=lambda value: value["id"])
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_json(self._index, document)

    def status(self) -> StorageStatus:
        items = self._load()["items"]
        total_bytes = 0
        for item in items:
            path = self.root.joinpath(*PurePosixPath(item["path"]).parts)
            if path.is_file() and not path.is_symlink():
                total_bytes += path.stat().st_size
        return StorageStatus(
            item_count=len(items),
            active_count=sum(
                item["task_state"] in _PROTECTED_TASK_STATES for item in items
            ),
            pinned_count=sum(bool(item["pinned"]) for item in items),
            expired_count=sum(bool(item["expired"]) for item in items),
            bytes_on_disk=total_bytes,
        )

    def pin(self, item_id: str, *, recommendation_ref: str | None = None) -> None:
        self.apply_pin(
            self.plan_pin(item_id, recommendation_ref=recommendation_ref)
        )

    def plan_pin(
        self, item_id: str, *, recommendation_ref: str | None = None
    ) -> PinPlan:
        document = self._load()
        item = _find(document, item_id)
        item["pinned"] = True
        if recommendation_ref is not None:
            item["recommendation_ref"] = recommendation_ref
        before = self._index.read_bytes()
        after = _json_bytes(document)
        return PinPlan(self.root, item_id, before, after)

    def apply_pin(self, plan: PinPlan) -> None:
        if plan.root != self.root:
            raise ValueError("pin plan belongs to another repository")
        if not self._index.is_file() or self._index.read_bytes() != plan.before:
            raise ValueError("storage index changed after review")
        _atomic_bytes(self._index, plan.after)

    def plan_prune(self, *, now: datetime | None = None) -> PrunePlan:
        current = now or datetime.now(UTC)
        ids: list[str] = []
        paths: list[str] = []
        reclaimable = 0
        for item in self._load()["items"]:
            if (
                item["pinned"]
                or item["recommendation_ref"] is not None
                or item["task_state"] in _PROTECTED_TASK_STATES
                or item["expired"]
            ):
                continue
            created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
            cutoff = current - timedelta(days=_RETENTION_DAYS[item["category"]])
            if created > cutoff:
                continue
            path = self.root.joinpath(*PurePosixPath(item["path"]).parts)
            ids.append(item["id"])
            paths.append(item["path"])
            if path.is_file() and not path.is_symlink():
                reclaimable += path.stat().st_size
        return PrunePlan(
            self.root,
            current.isoformat(),
            tuple(ids),
            tuple(paths),
            reclaimable,
        )

    def apply_prune(self, plan: PrunePlan) -> None:
        if plan.root != self.root:
            raise ValueError("prune plan belongs to another repository")
        document = self._load()
        planned = dict(zip(plan.item_ids, plan.paths, strict=True))
        for item_id, relative_path in planned.items():
            item = _find(document, item_id)
            if item["path"] != relative_path:
                raise ValueError(f"storage item changed after review: {item_id}")
            path = self.root.joinpath(*PurePosixPath(relative_path).parts)
            if path.is_symlink():
                raise ValueError(f"refusing to prune symlink: {relative_path}")
            path.unlink(missing_ok=True)
            item["expired"] = True
            item["expired_at"] = plan.generated_at
        _atomic_json(self._index, document)

    def _load(self) -> dict[str, Any]:
        if not self._index.exists():
            return {"schema_version": "1.0", "items": []}
        value = json.loads(self._index.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("items"), list):
            raise ValueError("storage index is invalid")
        return cast(dict[str, Any], value)


def _item_document(item: StorageItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "category": item.category,
        "path": item.path,
        "created_at": item.created_at,
        "task_state": item.task_state,
        "pinned": item.pinned,
        "recommendation_ref": item.recommendation_ref,
        "expired": item.expired,
    }


def _find(document: dict[str, Any], item_id: str) -> dict[str, Any]:
    for item in document["items"]:
        if item["id"] == item_id:
            return cast(dict[str, Any], item)
    raise ValueError(f"unknown storage item: {item_id}")


def _safe_relative(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("storage path must be safe and repository-relative")


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    _atomic_bytes(path, _json_bytes(document))


def _json_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_bytes(path: Path, content: bytes) -> None:
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
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "PinPlan",
    "PrunePlan",
    "StorageItem",
    "StorageManager",
    "StorageStatus",
]
