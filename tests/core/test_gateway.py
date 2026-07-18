from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from adaptive_harness.core.envelope import Acceptance, Requirement, TaskEnvelope
from adaptive_harness.core.gateway import (
    ApprovalPolicy,
    AuthorizedCommand,
    Capability,
    CapabilityDeniedError,
    CapabilityGateway,
    CapabilityInvariantError,
    ExecutionEnvironment,
    NetworkAccess,
    ProjectPolicy,
    Reversibility,
    ScopedApproval,
    SideEffect,
    WorkspaceSnapshot,
)
from adaptive_harness.core.store import TaskStore


def make_envelope(tmp_path: Path, *capability_ids: str) -> TaskEnvelope:
    return TaskEnvelope(
        schema_version="1.0",
        task_id="task-001",
        repository_id="repo-001",
        base_sha="a" * 40,
        worktree_path=tmp_path.resolve(),
        initial_dirty_state=(),
        harness_version="0.1.0",
        config_version="1.0",
        goal="Authorize a declared capability",
        non_goals=(),
        requirements=(Requirement(id="REQ-001", text="Enforce policy"),),
        allowed_scope=("src/", "tests/"),
        acceptances=(
            Acceptance(
                id="gateway-tests",
                required=True,
                covers=("REQ-001",),
                command_id="unit-tests",
                expected_exit_code=0,
                timeout_seconds=30,
                retry_budget=0,
                evidence_type="command_event",
            ),
        ),
        requested_capabilities=tuple(capability_ids),
        timeout_seconds=300,
        retry_budget=0,
        budget={"max_commands": 10},
        integration_mode="observe",
    )


def make_capability(**changes: Any) -> Capability:
    values: dict[str, Any] = {
        "id": "read-source",
        "argv": ("python", "-m", "compileall", "src"),
        "cwd": ".",
        "timeout_seconds": 30,
        "read_paths": ("src/",),
        "write_paths": (),
        "network": NetworkAccess.NONE,
        "listener": False,
        "side_effects": (SideEffect.FILESYSTEM_READ,),
        "reversibility": Reversibility.REVERSIBLE,
        "environment": ExecutionEnvironment.LOCAL,
        "approval_policy": ApprovalPolicy.AUTO,
        "max_executions": 2,
        "stop_on_exit_codes": (0,),
    }
    values.update(changes)
    return Capability(**values)


def make_gateway(
    tmp_path: Path,
    capability: Capability,
    *,
    policy: ProjectPolicy | None = None,
    snapshot: WorkspaceSnapshot | None = None,
) -> tuple[CapabilityGateway, TaskStore]:
    store = TaskStore(tmp_path / "data")
    store.create(make_envelope(tmp_path, capability.id))
    workspace = snapshot or WorkspaceSnapshot(
        repository_id="repo-001",
        base_sha="a" * 40,
        worktree_path=tmp_path.resolve(),
    )
    gateway = CapabilityGateway(
        store=store,
        capabilities=(capability,),
        policy=policy or ProjectPolicy(),
        workspace_probe=lambda: workspace,
    )
    return gateway, store


def make_approval(tmp_path: Path, capability: Capability) -> ScopedApproval:
    return ScopedApproval(
        id="approval-001",
        task_id="task-001",
        capability_id=capability.id,
        base_sha="a" * 40,
        worktree_path=tmp_path.resolve(),
        read_paths=capability.read_paths,
        write_paths=capability.write_paths,
        side_effects=capability.side_effects,
        network=capability.network,
        listener=capability.listener,
        environment=capability.environment,
        max_uses=1,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def test_declared_low_risk_capability_is_authorized_and_recorded(
    tmp_path: Path,
) -> None:
    capability = make_capability()
    gateway, store = make_gateway(tmp_path, capability)

    command = gateway.authorize("task-001", capability.id)

    assert command == AuthorizedCommand(
        task_id="task-001",
        capability_id=capability.id,
        argv=capability.argv,
        cwd=tmp_path.resolve(),
        timeout_seconds=30,
        approval_id=None,
        authorization_event_sequence=2,
        stop_on_exit_codes=(0,),
        read_paths=capability.read_paths,
        write_paths=capability.write_paths,
        network=capability.network,
        listener=capability.listener,
    )
    event = store.load("task-001").events[-1]
    assert event.type == "command.authorized"
    assert event.data["capability_id"] == capability.id
    assert event.data["approval_id"] is None


def test_unknown_or_unrequested_capability_is_denied(tmp_path: Path) -> None:
    capability = make_capability()
    gateway, _ = make_gateway(tmp_path, capability)

    with pytest.raises(CapabilityDeniedError, match="unknown capability"):
        gateway.authorize("task-001", "not-declared")

    extra = make_capability(id="not-requested")
    gateway = CapabilityGateway(
        store=gateway.store,
        capabilities=(capability, extra),
        policy=ProjectPolicy(),
        workspace_probe=gateway.workspace_probe,
    )
    with pytest.raises(CapabilityDeniedError, match="not requested"):
        gateway.authorize("task-001", extra.id)


def test_write_outside_envelope_scope_is_denied_even_with_approval(
    tmp_path: Path,
) -> None:
    capability = make_capability(
        id="write-secret",
        write_paths=("secrets/",),
        side_effects=(SideEffect.FILESYSTEM_WRITE,),
        approval_policy=ApprovalPolicy.ASK,
    )
    gateway, _ = make_gateway(tmp_path, capability)

    with pytest.raises(CapabilityDeniedError, match="allowed scope"):
        gateway.authorize(
            "task-001", capability.id, approvals=(make_approval(tmp_path, capability),)
        )


def test_workspace_identity_is_revalidated_before_authorization(
    tmp_path: Path,
) -> None:
    capability = make_capability()
    gateway, _ = make_gateway(
        tmp_path,
        capability,
        snapshot=WorkspaceSnapshot(
            repository_id="repo-001",
            base_sha="b" * 40,
            worktree_path=tmp_path.resolve(),
        ),
    )

    with pytest.raises(CapabilityDeniedError, match="base SHA"):
        gateway.authorize("task-001", capability.id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", "other-task"),
        ("base_sha", "b" * 40),
        ("write_paths", ("tests/",)),
        ("side_effects", (SideEffect.NETWORK,)),
    ],
)
def test_approval_cannot_be_reused_outside_its_binding(
    tmp_path: Path, field: str, value: Any
) -> None:
    capability = make_capability(
        id="network-write",
        write_paths=("src/",),
        network=NetworkAccess.OUTBOUND,
        side_effects=(SideEffect.NETWORK, SideEffect.FILESYSTEM_WRITE),
        approval_policy=ApprovalPolicy.ASK,
    )
    policy = ProjectPolicy(
        allowed_network=(NetworkAccess.NONE, NetworkAccess.OUTBOUND,)
    )
    gateway, _ = make_gateway(tmp_path, capability, policy=policy)
    approval = replace(make_approval(tmp_path, capability), **{field: value})

    with pytest.raises(CapabilityDeniedError, match="scoped approval"):
        gateway.authorize("task-001", capability.id, approvals=(approval,))


def test_approval_usage_count_is_persisted_in_task_history(tmp_path: Path) -> None:
    capability = make_capability(
        id="network-read",
        network=NetworkAccess.OUTBOUND,
        side_effects=(SideEffect.NETWORK,),
        approval_policy=ApprovalPolicy.ASK,
    )
    policy = ProjectPolicy(
        allowed_network=(NetworkAccess.NONE, NetworkAccess.OUTBOUND,)
    )
    gateway, _ = make_gateway(tmp_path, capability, policy=policy)
    approval = make_approval(tmp_path, capability)

    first = gateway.authorize(
        "task-001", capability.id, approvals=(approval,)
    )

    assert first.approval_id == approval.id
    with pytest.raises(CapabilityDeniedError, match="scoped approval"):
        gateway.authorize("task-001", capability.id, approvals=(approval,))


def test_task_retry_budget_limits_new_authorizations(tmp_path: Path) -> None:
    capability = make_capability(max_executions=5)
    gateway, store = make_gateway(tmp_path, capability)
    authorization = gateway.authorize("task-001", capability.id)
    store.append_event(
        "task-001",
        "command.started",
        {
            "authorization_event_sequence": (
                authorization.authorization_event_sequence
            ),
            "capability_id": capability.id,
            "attempt": 1,
        },
    )

    with pytest.raises(CapabilityDeniedError, match="retry budget"):
        gateway.authorize("task-001", capability.id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("network", "internet"),
        ("side_effects", ("mystery",)),
        ("argv", ()),
        ("cwd", "../outside"),
    ],
)
def test_capability_rejects_unknown_or_unsafe_declarations(
    field: str, value: Any
) -> None:
    with pytest.raises(CapabilityInvariantError):
        make_capability(**{field: value})
