import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError

from adaptive_harness.core.envelope import Acceptance, Requirement, TaskEnvelope
from adaptive_harness.core.gateway import (
    ApprovalPolicy,
    Capability,
    ExecutionEnvironment,
    NetworkAccess,
    Reversibility,
    SideEffect,
)
from adaptive_harness.core.store import TaskStore
from adaptive_harness.schemas import load_capabilities, load_schema, validator_for


def make_envelope(tmp_path: Path) -> TaskEnvelope:
    return TaskEnvelope(
        schema_version="1.0",
        task_id="schema-task",
        repository_id="schema-repo",
        base_sha="c" * 40,
        worktree_path=tmp_path.resolve(),
        initial_dirty_state=(),
        harness_version="0.1.0",
        config_version="1.0",
        goal="Validate external contracts",
        non_goals=(),
        requirements=(Requirement(id="REQ-001", text="Validate records"),),
        allowed_scope=("src/",),
        acceptances=(
            Acceptance(
                id="schema-tests",
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
        timeout_seconds=60,
        retry_budget=0,
        budget={"max_commands": 2},
        integration_mode="observe",
    )


@pytest.mark.parametrize(
    "schema_name", ["task-envelope", "task-record", "capabilities"]
)
def test_published_schema_is_valid_draft_2020_12(schema_name: str) -> None:
    Draft202012Validator.check_schema(load_schema(schema_name))


def test_task_envelope_matches_its_published_schema(tmp_path: Path) -> None:
    schema = load_schema("task-envelope")
    envelope = make_envelope(tmp_path).to_dict()

    Draft202012Validator(schema).validate(envelope)

    envelope["unknown"] = True
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(envelope)


def test_canonical_record_matches_its_published_schema(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "data")
    envelope = make_envelope(tmp_path)
    store.create(envelope)
    document = json.loads(
        store.record_path(envelope.task_id).read_text(encoding="utf-8")
    )

    validator_for("task-record").validate(document)


def test_capability_document_round_trips_through_external_contract() -> None:
    capability = Capability(
        id="unit-tests",
        argv=("pytest", "-q"),
        cwd=".",
        timeout_seconds=120,
        read_paths=("src/", "tests/"),
        write_paths=(),
        network=NetworkAccess.NONE,
        listener=False,
        side_effects=(SideEffect.FILESYSTEM_READ,),
        reversibility=Reversibility.REVERSIBLE,
        environment=ExecutionEnvironment.TEST,
        approval_policy=ApprovalPolicy.AUTO,
        max_executions=2,
        stop_on_exit_codes=(0,),
    )
    document: dict[str, Any] = {
        "schema_version": "1.0",
        "capabilities": [capability.to_dict()],
    }

    validator_for("capabilities").validate(document)
    assert load_capabilities(document) == (capability,)

    document["capabilities"][0]["shell"] = True
    with pytest.raises(ValidationError):
        validator_for("capabilities").validate(document)
