import os
import sys
from pathlib import Path

import pytest

from adaptive_harness.core.envelope import Acceptance, Requirement, TaskEnvelope
from adaptive_harness.core.executor import ExecutionLimits, Executor, WorkspaceLease
from adaptive_harness.core.gateway import (
    ApprovalPolicy,
    Capability,
    CapabilityGateway,
    ExecutionEnvironment,
    NetworkAccess,
    ProjectPolicy,
    Reversibility,
    SideEffect,
    WorkspaceSnapshot,
)
from adaptive_harness.core.store import TaskStore
from adaptive_harness.core.verifier import Verifier


def make_envelope(
    tmp_path: Path,
    *,
    requirements: tuple[Requirement, ...] | None = None,
    integration_mode: str = "observe",
) -> TaskEnvelope:
    return TaskEnvelope(
        schema_version="1.0",
        task_id="task-001",
        repository_id="repo-001",
        base_sha="a" * 40,
        worktree_path=tmp_path.resolve(),
        initial_dirty_state=(),
        harness_version="0.1.0",
        config_version="1.0",
        goal="Verify the completion gate",
        non_goals=(),
        requirements=requirements
        or (Requirement(id="REQ-001", text="Produce trusted evidence"),),
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
        requested_capabilities=("unit-tests",),
        timeout_seconds=300,
        retry_budget=1,
        budget={"max_commands": 4},
        integration_mode=integration_mode,
    )


def make_capability(exit_code: int = 0) -> Capability:
    return Capability(
        id="unit-tests",
        argv=(sys.executable, "-c", f"raise SystemExit({exit_code})"),
        cwd=".",
        timeout_seconds=5,
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


def prepare_task(
    tmp_path: Path,
    *,
    envelope: TaskEnvelope | None = None,
    exit_code: int = 0,
    changed_paths: tuple[str, ...] = (),
    secrets: tuple[str, ...] = (),
) -> tuple[TaskStore, Verifier, WorkspaceSnapshot]:
    envelope = envelope or make_envelope(tmp_path)
    snapshot = WorkspaceSnapshot(
        repository_id=envelope.repository_id,
        base_sha=envelope.base_sha,
        worktree_path=envelope.worktree_path,
    )
    store = TaskStore(tmp_path / "data")
    store.create(envelope)
    capability = make_capability(exit_code)
    gateway = CapabilityGateway(
        store=store,
        capabilities=(capability,),
        policy=ProjectPolicy(),
        workspace_probe=lambda: snapshot,
    )
    executor = Executor(
        store=store,
        artifact_root=tmp_path / "artifacts",
        workspace_probe=lambda: snapshot,
        limits=ExecutionLimits(),
        secrets=secrets,
    )
    executor.execute(gateway.authorize(envelope.task_id, capability.id))
    verifier = Verifier(
        store=store,
        artifact_root=tmp_path / "artifacts",
        workspace_probe=lambda: snapshot,
        diff_probe=lambda _: changed_paths,
        secrets=secrets,
    )
    return store, verifier, snapshot


def test_all_required_evidence_completes_task(tmp_path: Path) -> None:
    store, verifier, _ = prepare_task(tmp_path)

    report = verifier.verify("task-001")

    assert report.outcome == "completed"
    assert report.issues == ()
    assert report.acceptances[0].status == "passed"
    assert store.load("task-001").state == "completed"


def test_structural_requirement_coverage_cannot_be_accepted_as_risk(
    tmp_path: Path,
) -> None:
    envelope = make_envelope(
        tmp_path,
        requirements=(
            Requirement(id="REQ-001", text="Produce trusted evidence"),
            Requirement(id="REQ-002", text="Remain covered"),
        ),
    )
    store, verifier, _ = prepare_task(tmp_path, envelope=envelope)

    report = verifier.verify(
        "task-001", accept_risk=True, risk_reason="Ship without coverage"
    )

    assert report.outcome == "rejected"
    assert any(issue.code == "requirement_uncovered" for issue in report.issues)
    assert store.load("task-001").state == "draft"


def test_failed_required_check_can_only_enter_accepted_with_risk(
    tmp_path: Path,
) -> None:
    store, verifier, _ = prepare_task(tmp_path, exit_code=1)

    rejected = verifier.verify("task-001")
    accepted = verifier.verify(
        "task-001",
        accept_risk=True,
        risk_reason="Known test failure accepted by the user",
    )

    assert rejected.outcome == "rejected"
    assert accepted.outcome == "accepted_with_risk"
    assert store.load("task-001").state == "accepted_with_risk"


def test_diff_outside_allowed_scope_is_non_waivable(tmp_path: Path) -> None:
    store, verifier, _ = prepare_task(
        tmp_path, changed_paths=("src/allowed.py", "secrets.txt")
    )

    report = verifier.verify(
        "task-001", accept_risk=True, risk_reason="Accept the extra file"
    )

    assert report.outcome == "rejected"
    assert any(issue.code == "diff_out_of_scope" for issue in report.issues)
    assert store.load("task-001").state == "draft"


def test_workspace_change_prevents_completion(tmp_path: Path) -> None:
    store, _, snapshot = prepare_task(tmp_path)
    verifier = Verifier(
        store=store,
        artifact_root=tmp_path / "artifacts",
        workspace_probe=lambda: WorkspaceSnapshot(
            repository_id=snapshot.repository_id,
            base_sha="b" * 40,
            worktree_path=snapshot.worktree_path,
        ),
        diff_probe=lambda _: (),
    )

    report = verifier.verify(
        "task-001", accept_risk=True, risk_reason="Ignore changed SHA"
    )

    assert report.outcome == "rejected"
    assert any(issue.code == "workspace_mismatch" for issue in report.issues)


def test_artifact_symlink_escape_prevents_completion(tmp_path: Path) -> None:
    store, verifier, _ = prepare_task(tmp_path)
    record = store.load("task-001")
    stdout_path = Path(record.events[-1].data["stdout_artifact"])
    outside = tmp_path / "outside.txt"
    outside.write_text("forged", encoding="utf-8")
    stdout_path.unlink()
    stdout_path.symlink_to(outside)

    report = verifier.verify(
        "task-001", accept_risk=True, risk_reason="Trust the symlink"
    )

    assert report.outcome == "rejected"
    assert any(issue.code == "artifact_escape" for issue in report.issues)


def test_secret_in_artifact_is_non_waivable(tmp_path: Path) -> None:
    secret = "verifier-secret"
    store, verifier, _ = prepare_task(tmp_path, secrets=(secret,))
    record = store.load("task-001")
    stdout_path = Path(record.events[-1].data["stdout_artifact"])
    stdout_path.write_text(secret, encoding="utf-8")

    report = verifier.verify(
        "task-001", accept_risk=True, risk_reason="Ignore leaked secret"
    )

    assert report.outcome == "rejected"
    assert any(issue.code == "secret_leak" for issue in report.issues)


def test_evidence_from_older_envelope_revision_is_stale(tmp_path: Path) -> None:
    store, verifier, _ = prepare_task(tmp_path)
    store.amend("task-001", goal="A materially revised task goal")

    report = verifier.verify("task-001")

    assert report.outcome == "rejected"
    assert report.acceptances[0].status == "stale"


@pytest.mark.skipif(
    os.environ.get("HARNESS_RUN_OS_SANDBOX_E2E") != "1",
    reason="requires host sandbox-exec outside the test runner sandbox",
)
def test_enforced_task_accepts_real_sandboxed_executor_evidence(
    tmp_path: Path,
) -> None:
    envelope = make_envelope(tmp_path, integration_mode="enforced")
    store, verifier, _ = prepare_task(tmp_path, envelope=envelope)

    report = verifier.verify("task-001")

    assert report.outcome == "completed"
    assert store.load("task-001").state == "completed"


def test_forged_command_event_is_not_evidence(tmp_path: Path) -> None:
    envelope = make_envelope(tmp_path)
    snapshot = WorkspaceSnapshot(
        repository_id=envelope.repository_id,
        base_sha=envelope.base_sha,
        worktree_path=envelope.worktree_path,
    )
    store = TaskStore(tmp_path / "data")
    store.create(envelope)
    store.append_event(
        envelope.task_id,
        "command.finished",
        {
            "producer": "adaptive_harness.executor",
            "authorization_event_sequence": 1,
            "capability_id": "unit-tests",
            "status": "succeeded",
            "exit_code": 0,
            "timed_out": False,
            "cancelled": False,
        },
    )
    verifier = Verifier(
        store=store,
        artifact_root=tmp_path / "artifacts",
        workspace_probe=lambda: snapshot,
        diff_probe=lambda _: (),
    )

    report = verifier.verify("task-001")

    assert report.outcome == "rejected"
    assert report.acceptances[0].status == "untrusted"
    assert any(issue.code == "untrusted_evidence" for issue in report.issues)


def test_active_workspace_lease_is_non_waivable(tmp_path: Path) -> None:
    store, verifier, snapshot = prepare_task(tmp_path)

    with WorkspaceLease(
        tmp_path / "artifacts" / "leases",
        snapshot.worktree_path,
        "other-task",
    ):
        report = verifier.verify(
            "task-001", accept_risk=True, risk_reason="Ignore active executor"
        )

    assert report.outcome == "rejected"
    assert report.acceptances == ()
    assert report.issues[0].code == "lease_conflict"
    assert store.load("task-001").state == "draft"
