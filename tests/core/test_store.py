import json
from pathlib import Path

import pytest

from adaptive_harness.core.envelope import Acceptance, Requirement, TaskEnvelope
from adaptive_harness.core.store import (
    RecordCorruptedError,
    TaskAlreadyExistsError,
    TaskRecord,
    TaskStore,
)


def make_envelope(tmp_path: Path) -> TaskEnvelope:
    return TaskEnvelope(
        schema_version="1.0",
        task_id="task-001",
        repository_id="repo-001",
        base_sha="a" * 40,
        worktree_path=tmp_path.resolve(),
        initial_dirty_state=(),
        harness_version="0.1.0",
        config_version="1.0",
        goal="Persist task state",
        non_goals=(),
        requirements=(Requirement(id="REQ-001", text="Store an envelope"),),
        allowed_scope=("src/", "tests/"),
        acceptances=(
            Acceptance(
                id="unit-tests",
                required=True,
                covers=("REQ-001",),
                command_id="unit-tests",
                expected_exit_code=0,
                timeout_seconds=30,
                retry_budget=0,
                evidence_type="command_event",
            ),
        ),
        requested_capabilities=("filesystem.read", "filesystem.write"),
        timeout_seconds=300,
        retry_budget=1,
        budget={"max_commands": 10},
        integration_mode="observe",
    )


def test_create_and_load_task_record(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "data")
    envelope = make_envelope(tmp_path)

    created = store.create(envelope)
    loaded = store.load(envelope.task_id)

    assert created == loaded
    assert loaded.state == "draft"
    assert loaded.current_envelope == envelope
    assert [event.type for event in loaded.events] == ["task.created"]


def test_create_refuses_to_replace_an_existing_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "data")
    envelope = make_envelope(tmp_path)
    store.create(envelope)

    with pytest.raises(TaskAlreadyExistsError):
        store.create(envelope)


def test_amend_appends_revision_and_history(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "data")
    envelope = make_envelope(tmp_path)
    store.create(envelope)

    amended = store.amend(
        envelope.task_id,
        goal="Persist append-only task state",
        requested_capabilities=(
            "filesystem.read",
            "filesystem.write",
            "process.execute",
        ),
    )

    assert [item.revision for item in amended.envelope_revisions] == [1, 2]
    assert amended.current_envelope.goal == "Persist append-only task state"
    assert [event.type for event in amended.events] == [
        "task.created",
        "task.amended",
    ]


def test_cancel_records_terminal_state_without_removing_history(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "data")
    envelope = make_envelope(tmp_path)
    store.create(envelope)
    store.amend(envelope.task_id, goal="Updated goal")

    cancelled = store.cancel(envelope.task_id, reason="User cancelled")

    assert cancelled.state == "abandoned"
    assert len(cancelled.envelope_revisions) == 2
    assert cancelled.events[-1].type == "task.cancelled"
    assert cancelled.events[-1].data == {"reason": "User cancelled"}


def test_modified_record_is_detected_as_corrupt(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "data")
    envelope = make_envelope(tmp_path)
    store.create(envelope)
    record_path = store.record_path(envelope.task_id)
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload["state"] = "completed"
    record_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RecordCorruptedError, match="integrity"):
        store.load(envelope.task_id)


def test_failed_atomic_replace_leaves_previous_record_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TaskStore(tmp_path / "data")
    envelope = make_envelope(tmp_path)
    original = store.create(envelope)

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr("adaptive_harness.core.store.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated interruption"):
        store.amend(envelope.task_id, goal="This write must not become visible")

    assert store.load(envelope.task_id) == original


def test_record_rejects_unknown_state(tmp_path: Path) -> None:
    record = TaskStore(tmp_path / "data").create(make_envelope(tmp_path))

    with pytest.raises(RecordCorruptedError, match="state"):
        TaskRecord(
            schema_version=record.schema_version,
            task_id=record.task_id,
            state="claimed-complete",
            envelope_revisions=record.envelope_revisions,
            events=record.events,
        )


def test_record_rejects_forged_revision_identity(tmp_path: Path) -> None:
    envelope = make_envelope(tmp_path)
    forged_revision = TaskEnvelope.from_dict(
        {
            **envelope.to_dict(),
            "base_sha": "b" * 40,
            "revision": 2,
        }
    )

    with pytest.raises(RecordCorruptedError, match="base_sha"):
        TaskRecord(
            schema_version="1.0",
            task_id=envelope.task_id,
            state="draft",
            envelope_revisions=(envelope, forged_revision),
            events=TaskStore(tmp_path / "data").create(envelope).events,
        )
