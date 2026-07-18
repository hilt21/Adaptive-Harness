from pathlib import Path
from typing import Any

import pytest

from adaptive_harness.core.envelope import (
    Acceptance,
    EnvelopeInvariantError,
    Requirement,
    TaskEnvelope,
)

BASE_SHA = "a" * 40


def make_envelope(tmp_path: Path) -> TaskEnvelope:
    return TaskEnvelope(
        schema_version="1.0",
        task_id="task-001",
        repository_id="repo-001",
        base_sha=BASE_SHA,
        worktree_path=tmp_path.resolve(),
        initial_dirty_state=("?? notes.txt",),
        harness_version="0.1.0",
        config_version="1.0",
        goal="Implement the task record",
        non_goals=("Implement the executor",),
        requirements=(Requirement(id="REQ-001", text="Persist task state"),),
        allowed_scope=("src/adaptive_harness/core/", "tests/core/"),
        acceptances=(
            Acceptance(
                id="record-tests",
                required=True,
                covers=("REQ-001",),
                command_id="unit-tests",
                expected_exit_code=0,
                timeout_seconds=30,
                retry_budget=0,
                evidence_type="command_event",
            ),
        ),
        requested_capabilities=("filesystem.read",),
        timeout_seconds=300,
        retry_budget=1,
        budget={"max_commands": 10},
        integration_mode="observe",
    )


@pytest.mark.parametrize("base_sha", ["abc123", "g" * 40, "a" * 41])
def test_envelope_requires_a_full_git_sha(tmp_path: Path, base_sha: str) -> None:
    envelope = make_envelope(tmp_path)

    with pytest.raises(EnvelopeInvariantError, match="base_sha"):
        envelope.amend(base_sha=base_sha)


def test_envelope_requires_an_absolute_worktree(tmp_path: Path) -> None:
    envelope = make_envelope(tmp_path)

    with pytest.raises(EnvelopeInvariantError, match="worktree_path"):
        envelope.amend(worktree_path=Path("relative/path"))


def test_amendment_preserves_base_sha_and_required_acceptance(tmp_path: Path) -> None:
    envelope = make_envelope(tmp_path)

    with pytest.raises(EnvelopeInvariantError, match="base_sha is immutable"):
        envelope.amend(base_sha="b" * 40)

    with pytest.raises(EnvelopeInvariantError, match="required acceptance"):
        envelope.amend(acceptances=())


def test_amendment_cannot_silently_reduce_requested_capabilities(
    tmp_path: Path,
) -> None:
    envelope = make_envelope(tmp_path).amend(
        requested_capabilities=("filesystem.read", "filesystem.write")
    )

    with pytest.raises(EnvelopeInvariantError, match="requested capabilities"):
        envelope.amend(requested_capabilities=("filesystem.read",))


def test_amendment_is_a_new_revision_and_round_trips(tmp_path: Path) -> None:
    original = make_envelope(tmp_path)

    amended = original.amend(
        goal="Persist an append-only task record",
        requested_capabilities=("filesystem.read", "filesystem.write"),
    )

    assert original.revision == 1
    assert amended.revision == 2
    assert amended.goal == "Persist an append-only task record"
    assert TaskEnvelope.from_dict(amended.to_dict()) == amended


def test_acceptance_uses_the_prd_external_contract(tmp_path: Path) -> None:
    acceptance = make_envelope(tmp_path).to_dict()["acceptances"][0]

    assert acceptance["expected"] == {"exit_code": 0}
    assert "expected_exit_code" not in acceptance


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("required", "yes"),
        ("expected_exit_code", True),
        ("timeout_seconds", True),
        ("retry_budget", -1),
    ],
)
def test_acceptance_rejects_values_that_only_look_typed(
    field: str, value: Any
) -> None:
    arguments: dict[str, Any] = {
        "id": "unit-tests",
        "required": True,
        "covers": ("REQ-001",),
        "command_id": "unit-tests",
        "expected_exit_code": 0,
        "timeout_seconds": 30,
        "retry_budget": 0,
        "evidence_type": "command_event",
    }
    arguments[field] = value

    with pytest.raises(EnvelopeInvariantError):
        Acceptance(**arguments)


def test_envelope_identity_snapshot_is_immutable(tmp_path: Path) -> None:
    envelope = make_envelope(tmp_path)

    with pytest.raises(EnvelopeInvariantError, match="worktree_path is immutable"):
        envelope.amend(worktree_path=(tmp_path / "other").resolve())

    with pytest.raises(
        EnvelopeInvariantError, match="initial_dirty_state is immutable"
    ):
        envelope.amend(initial_dirty_state=())


def test_amendment_cannot_reduce_allowed_scope(tmp_path: Path) -> None:
    envelope = make_envelope(tmp_path)

    with pytest.raises(EnvelopeInvariantError, match="allowed scope"):
        envelope.amend(allowed_scope=("src/adaptive_harness/core/",))


def test_external_acceptance_rejects_unknown_expected_fields(tmp_path: Path) -> None:
    value = make_envelope(tmp_path).to_dict()
    value["acceptances"][0]["expected"]["stdout"] = "ok"

    with pytest.raises(EnvelopeInvariantError, match="acceptance expected"):
        TaskEnvelope.from_dict(value)
