from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread

import pytest

from adaptive_harness.storage import StorageItem, StorageManager
from adaptive_harness.storage.location import project_data_lock
from tests.storage_support import committed_storage_locator


def _manager(tmp_path: Path) -> StorageManager:
    locator = committed_storage_locator(
        tmp_path / "project", tmp_path / "data"
    )
    return StorageManager(
        locator.location().project_data,
        storage_locator=locator,
    )


def _register_file(
    manager: StorageManager,
    *,
    item_id: str,
    category: str,
    age_days: int,
    task_state: str = "completed",
) -> Path:
    path = manager.root / category / f"{item_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("payload", encoding="utf-8")
    created = datetime(2026, 7, 16, tzinfo=UTC) - timedelta(days=age_days)
    manager.register(
        StorageItem(
            item_id,
            category,
            str(path.relative_to(manager.root)),
            created.isoformat(),
            task_state,
        )
    )
    return path


def test_layered_retention_protects_active_and_pinned_items(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    old_artifact = _register_file(
        manager, item_id="old-artifact", category="artifact", age_days=8
    )
    recent_artifact = _register_file(
        manager, item_id="recent-artifact", category="artifact", age_days=6
    )
    old_record = _register_file(
        manager, item_id="old-record", category="record", age_days=31
    )
    active_record = _register_file(
        manager,
        item_id="active-record",
        category="record",
        age_days=100,
        task_state="active",
    )
    manager.pin("old-record", recommendation_ref="recommendation:1")

    plan = manager.plan_prune(now=datetime(2026, 7, 16, tzinfo=UTC))

    assert plan.item_ids == ("old-artifact",)
    assert plan.bytes_reclaimable == len("payload")
    manager.apply_prune(plan)
    assert not old_artifact.exists()
    assert recent_artifact.exists()
    assert old_record.exists()
    assert active_record.exists()
    status = manager.status()
    assert status.expired_count == 1
    assert status.active_count == 1
    assert status.pinned_count == 1


def test_feedback_and_risk_items_use_longer_retention_tiers(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    minimal = _register_file(
        manager, item_id="minimal", category="minimal_episode", age_days=31
    )
    research = _register_file(
        manager, item_id="research", category="research_episode", age_days=31
    )
    risk = _register_file(
        manager, item_id="risk", category="accepted_with_risk", age_days=89
    )

    plan = manager.plan_prune(now=datetime(2026, 7, 16, tzinfo=UTC))
    manager.apply_prune(plan)

    assert not minimal.exists()
    assert research.exists()
    assert risk.exists()


def test_storage_writes_wait_for_project_data_lock(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    waiting = Event()
    written = Event()

    def register() -> None:
        waiting.set()
        manager.register(
            StorageItem(
                "record-1",
                "record",
                "records/record-1.json",
                "2026-07-16T00:00:00+00:00",
                "completed",
            )
        )
        written.set()

    with project_data_lock(manager.root):
        thread = Thread(target=register)
        thread.start()
        assert waiting.wait(1)
        assert not written.wait(0.1)
    thread.join(timeout=1)

    assert written.is_set()


def test_storage_writer_requires_clone_binding(tmp_path: Path) -> None:
    manager = StorageManager(tmp_path, "repo-1")

    with pytest.raises(ValueError, match="storage locator is required"):
        manager.status()
    with pytest.raises(ValueError, match="storage locator is required"):
        manager.register(
            StorageItem(
                "record-1",
                "record",
                "records/record-1.json",
                "2026-07-16T00:00:00+00:00",
                "completed",
            )
        )
