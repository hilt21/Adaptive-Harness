import sys
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest

import adaptive_harness.core.executor as executor_module
from adaptive_harness.core.envelope import Acceptance, Requirement, TaskEnvelope
from adaptive_harness.core.executor import (
    ExecutionLimits,
    ExecutionRejectedError,
    Executor,
    LeaseConflictError,
    WorkspaceLease,
)
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


def make_envelope(tmp_path: Path, capability_id: str) -> TaskEnvelope:
    return TaskEnvelope(
        schema_version="1.0",
        task_id="task-001",
        repository_id="repo-001",
        base_sha="a" * 40,
        worktree_path=tmp_path.resolve(),
        initial_dirty_state=(),
        harness_version="0.1.0",
        config_version="1.0",
        goal="Execute an authorized command",
        non_goals=(),
        requirements=(Requirement(id="REQ-001", text="Capture evidence"),),
        allowed_scope=("src/", "tests/"),
        acceptances=(
            Acceptance(
                id="executor-tests",
                required=True,
                covers=("REQ-001",),
                command_id=capability_id,
                expected_exit_code=0,
                timeout_seconds=30,
                retry_budget=0,
                evidence_type="command_event",
            ),
        ),
        requested_capabilities=(capability_id,),
        timeout_seconds=300,
        retry_budget=1,
        budget={"max_commands": 10},
        integration_mode="observe",
    )


def make_capability(
    argv: tuple[str, ...], *, timeout_seconds: int = 5
) -> Capability:
    return Capability(
        id="execute-test",
        argv=argv,
        cwd=".",
        timeout_seconds=timeout_seconds,
        read_paths=("src/", "tests/"),
        write_paths=(),
        network=NetworkAccess.NONE,
        listener=False,
        side_effects=(SideEffect.FILESYSTEM_READ,),
        reversibility=Reversibility.REVERSIBLE,
        environment=ExecutionEnvironment.TEST,
        approval_policy=ApprovalPolicy.AUTO,
        max_executions=5,
        stop_on_exit_codes=(0,),
    )


def prepare_execution(
    tmp_path: Path,
    capability: Capability,
    *,
    limits: ExecutionLimits | None = None,
    secrets: tuple[str, ...] = (),
) -> tuple[Executor, CapabilityGateway, TaskStore, WorkspaceSnapshot]:
    snapshot = WorkspaceSnapshot(
        repository_id="repo-001",
        base_sha="a" * 40,
        worktree_path=tmp_path.resolve(),
    )
    store = TaskStore(tmp_path / "data")
    store.create(make_envelope(tmp_path, capability.id))
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
        limits=limits or ExecutionLimits(),
        secrets=secrets,
    )
    return executor, gateway, store, snapshot


def test_executor_captures_redacted_stdout_stderr_and_trusted_event(
    tmp_path: Path,
) -> None:
    secret = "token-super-secret"
    capability = make_capability(
        (
            sys.executable,
            "-c",
            f"import sys; print('{secret}'); print('{secret}', file=sys.stderr)",
        )
    )
    executor, gateway, store, _ = prepare_execution(
        tmp_path, capability, secrets=(secret,)
    )
    authorization = gateway.authorize("task-001", capability.id)

    result = executor.execute(authorization)

    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.stdout_artifact.read_text(encoding="utf-8").strip() == "[REDACTED]"
    assert result.stderr_artifact.read_text(encoding="utf-8").strip() == "[REDACTED]"
    record = store.load("task-001")
    assert record.events[-2].type == "command.started"
    assert record.events[-1].type == "command.finished"
    assert record.events[-1].data["authorization_event_sequence"] == (
        authorization.authorization_event_sequence
    )


def test_executor_times_out_and_terminates_process_group(tmp_path: Path) -> None:
    capability = make_capability(
        (sys.executable, "-c", "import time; time.sleep(5)"),
        timeout_seconds=1,
    )
    executor, gateway, _, _ = prepare_execution(tmp_path, capability)

    result = executor.execute(gateway.authorize("task-001", capability.id))

    assert result.status == "timed_out"
    assert result.timed_out is True
    assert result.exit_code is not None


def test_executor_honors_cancellation(tmp_path: Path) -> None:
    capability = make_capability(
        (sys.executable, "-c", "import time; time.sleep(5)")
    )
    executor, gateway, _, _ = prepare_execution(tmp_path, capability)
    cancellation = Event()
    cancellation.set()

    result = executor.execute(
        gateway.authorize("task-001", capability.id),
        cancellation=cancellation,
    )

    assert result.status == "cancelled"
    assert result.cancelled is True


def test_executor_limits_output_before_writing_artifacts(tmp_path: Path) -> None:
    capability = make_capability(
        (sys.executable, "-c", "print('x' * 10000)")
    )
    executor, gateway, _, _ = prepare_execution(
        tmp_path,
        capability,
        limits=ExecutionLimits(max_output_bytes=128, summary_characters=32),
    )

    result = executor.execute(gateway.authorize("task-001", capability.id))

    assert result.status == "output_limit_exceeded"
    assert result.output_truncated is True
    assert result.stdout_artifact.stat().st_size <= 128


def test_missing_executable_is_an_environment_failure(tmp_path: Path) -> None:
    capability = make_capability(("/definitely/not/a/program",))
    executor, gateway, store, _ = prepare_execution(tmp_path, capability)

    result = executor.execute(gateway.authorize("task-001", capability.id))

    assert result.status == "environment_failure"
    assert result.exit_code is None
    assert store.load("task-001").events[-1].data["status"] == (
        "environment_failure"
    )


def test_executor_rejects_forged_or_replayed_authorization(tmp_path: Path) -> None:
    capability = make_capability((sys.executable, "-c", "pass"))
    executor, gateway, _, _ = prepare_execution(tmp_path, capability)
    authorization = gateway.authorize("task-001", capability.id)
    forged = replace(authorization, authorization_event_sequence=999)

    with pytest.raises(ExecutionRejectedError, match="authorization"):
        executor.execute(forged)

    executor.execute(authorization)
    with pytest.raises(ExecutionRejectedError, match="already consumed"):
        executor.execute(authorization)


def test_enforced_executor_fails_closed_without_host_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = make_capability((sys.executable, "-c", "pass"))
    executor, gateway, store, _ = prepare_execution(tmp_path, capability)
    store.amend("task-001", integration_mode="enforced")
    authorization = gateway.authorize("task-001", capability.id)
    monkeypatch.setattr("adaptive_harness.core.executor.shutil.which", lambda _: None)

    with pytest.raises(ExecutionRejectedError, match="unavailable|no verified"):
        executor.execute(authorization)


def test_linux_sandbox_rebinds_tmp_worktree_after_private_tmpfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    capability = make_capability((sys.executable, "-c", "pass"))
    _, gateway, _, _ = prepare_execution(tmp_path, capability)
    authorization = gateway.authorize("task-001", capability.id)
    monkeypatch.setattr("adaptive_harness.core.executor.sys.platform", "linux")
    monkeypatch.setattr(
        "adaptive_harness.core.executor.shutil.which", lambda _: "/usr/bin/bwrap"
    )

    command = executor_module._sandbox_command(authorization)

    tmpfs_index = command.index("--tmpfs")
    worktree_bind_index = command.index(str(tmp_path.resolve()), tmpfs_index)
    assert worktree_bind_index > tmpfs_index
    assert command[worktree_bind_index - 1] == "--ro-bind"


def test_workspace_lease_prevents_concurrent_execution(tmp_path: Path) -> None:
    capability = make_capability((sys.executable, "-c", "pass"))
    executor, gateway, _, snapshot = prepare_execution(tmp_path, capability)
    authorization = gateway.authorize("task-001", capability.id)

    with WorkspaceLease(
        tmp_path / "artifacts" / "leases",
        snapshot.worktree_path,
        "other-task",
    ), pytest.raises(LeaseConflictError):
        executor.execute(authorization)


def test_artifact_symlink_cannot_escape_the_artifact_root(tmp_path: Path) -> None:
    capability = make_capability((sys.executable, "-c", "print('unsafe')"))
    executor, gateway, _, _ = prepare_execution(tmp_path, capability)
    authorization = gateway.authorize("task-001", capability.id)
    outside = tmp_path / "outside"
    outside.mkdir()
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir(exist_ok=True)
    (artifact_root / "task-001").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ExecutionRejectedError, match="artifact"):
        executor.execute(authorization)

    assert not (outside / "commands").exists()
