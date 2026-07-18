"""Application service that binds task records to project-local configuration."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from adaptive_harness import __version__
from adaptive_harness.core.envelope import Acceptance, Requirement, TaskEnvelope
from adaptive_harness.core.executor import CommandResult, ExecutionLimits, Executor
from adaptive_harness.core.gateway import (
    Capability,
    CapabilityGateway,
    ExecutionEnvironment,
    NetworkAccess,
    ProjectPolicy,
    ScopedApproval,
    SideEffect,
)
from adaptive_harness.core.store import TaskRecord, TaskStore
from adaptive_harness.core.verifier import VerificationReport, Verifier
from adaptive_harness.core.workspace import GitWorkspace
from adaptive_harness.modules import ModuleManager
from adaptive_harness.schemas import load_capabilities, validator_for


class TaskService:
    """Create and operate tasks without exposing record mutation primitives."""

    def __init__(self, root: Path, data_root: Path) -> None:
        self.root = Path(root).resolve()
        self.data_root = Path(data_root).resolve()
        self.workspace = GitWorkspace(self.root)
        snapshot = self.workspace.snapshot()
        self.project_data = self.data_root / "projects" / snapshot.repository_id
        self.store = TaskStore(self.project_data)
        self.artifact_root = self.project_data / "artifacts"

    def start(
        self,
        *,
        goal: str,
        allowed_scope: tuple[str, ...],
        requirements: tuple[str, ...] = (),
        capability_ids: tuple[str, ...] = (),
        task_traits: tuple[str, ...] = (),
        manually_requested_modules: tuple[str, ...] = (),
        task_id: str | None = None,
        timeout_seconds: int = 120,
        retry_budget: int = 1,
    ) -> TaskRecord:
        facts = self.workspace.inspect()
        config = self._config()
        capabilities = self._capabilities()
        available = {item.id for item in capabilities}
        selected = capability_ids or tuple(sorted(available))
        if not selected:
            raise ValueError(
                "task requires at least one declared acceptance capability"
            )
        unknown = set(selected) - available
        if unknown:
            raise ValueError(f"task requested unknown capabilities: {sorted(unknown)}")
        activation_decisions = ModuleManager(self.root).activation_decisions(
            task_traits=frozenset(task_traits),
            available_capabilities=frozenset(selected),
            manually_requested=frozenset(manually_requested_modules),
        )
        requirement_values = requirements or (goal,)
        requirement_objects = tuple(
            Requirement(f"requirement-{index}", text)
            for index, text in enumerate(requirement_values, start=1)
        )
        covered = tuple(item.id for item in requirement_objects)
        acceptance_objects = tuple(
            Acceptance(
                id=f"acceptance-{index}",
                required=True,
                covers=covered,
                command_id=capability_id,
                expected_exit_code=0,
                timeout_seconds=timeout_seconds,
                retry_budget=retry_budget,
                evidence_type="command_event",
            )
            for index, capability_id in enumerate(selected, start=1)
        )
        identifier = task_id or _task_id()
        envelope = TaskEnvelope(
            schema_version="1.0",
            task_id=identifier,
            repository_id=facts.snapshot.repository_id,
            base_sha=facts.snapshot.base_sha,
            worktree_path=facts.snapshot.worktree_path,
            initial_dirty_state=facts.dirty_state,
            harness_version=__version__,
            config_version=cast(str, config["schema_version"]),
            goal=goal,
            non_goals=(),
            requirements=requirement_objects,
            allowed_scope=allowed_scope,
            acceptances=acceptance_objects,
            requested_capabilities=selected,
            timeout_seconds=timeout_seconds,
            retry_budget=retry_budget,
            budget={"max_commands": max(1, len(selected) * (retry_budget + 1))},
            integration_mode=cast(str, config["adapter"]["mode"]),
        )
        record = self.store.create(envelope)
        for decision in activation_decisions:
            record = self.store.append_event(
                identifier,
                "module.activation",
                {
                    "module_id": decision.module_id,
                    "state": decision.state.value,
                    "reason": decision.reason,
                    "task_traits": sorted(set(task_traits)),
                    "context": decision.context,
                },
            )
        return record

    def show(self, task_id: str) -> TaskRecord:
        return self.store.load(task_id)

    def amend(
        self,
        task_id: str,
        *,
        goal: str | None = None,
        add_scope: tuple[str, ...] = (),
        add_capabilities: tuple[str, ...] = (),
    ) -> TaskRecord:
        current = self.store.load(task_id).current_envelope
        changes: dict[str, Any] = {}
        if goal is not None:
            changes["goal"] = goal
        if add_scope:
            changes["allowed_scope"] = tuple(
                dict.fromkeys((*current.allowed_scope, *add_scope))
            )
        if add_capabilities:
            available = {item.id for item in self._capabilities()}
            unknown = set(add_capabilities) - available
            if unknown:
                raise ValueError(f"unknown capabilities: {sorted(unknown)}")
            selected = tuple(
                dict.fromkeys((*current.requested_capabilities, *add_capabilities))
            )
            changes["requested_capabilities"] = selected
            acceptances = list(current.acceptances)
            existing_commands = {item.command_id for item in acceptances}
            covered = tuple(item.id for item in current.requirements)
            for capability_id in add_capabilities:
                if capability_id not in existing_commands:
                    acceptances.append(
                        Acceptance(
                            id=f"acceptance-{len(acceptances) + 1}",
                            required=True,
                            covers=covered,
                            command_id=capability_id,
                            expected_exit_code=0,
                            timeout_seconds=current.timeout_seconds,
                            retry_budget=current.retry_budget,
                            evidence_type="command_event",
                        )
                    )
            changes["acceptances"] = tuple(acceptances)
        if not changes:
            raise ValueError("task amendment has no changes")
        return self.store.amend(task_id, **changes)

    def cancel(self, task_id: str, *, reason: str) -> TaskRecord:
        return self.store.cancel(task_id, reason=reason)

    def verify(
        self,
        task_id: str,
        *,
        accept_risk: bool = False,
        risk_reason: str | None = None,
    ) -> VerificationReport:
        return Verifier(
            store=self.store,
            artifact_root=self.artifact_root,
            workspace_probe=self.workspace.snapshot,
            diff_probe=self.workspace.diff_for,
        ).verify(
            task_id,
            accept_risk=accept_risk,
            risk_reason=risk_reason,
        )

    def plan_approval(
        self,
        task_id: str,
        capability_id: str,
        *,
        max_uses: int = 1,
        valid_for_minutes: int = 15,
    ) -> ScopedApproval:
        if max_uses < 1:
            raise ValueError("approval max_uses must be positive")
        if valid_for_minutes < 1:
            raise ValueError("approval validity must be positive")
        record = self.store.load(task_id)
        capability = self._capability(capability_id)
        if capability_id not in record.current_envelope.requested_capabilities:
            raise ValueError(
                f"capability was not requested by the task: {capability_id}"
            )
        snapshot = self.workspace.snapshot()
        envelope = record.current_envelope
        if (
            snapshot.repository_id != envelope.repository_id
            or snapshot.base_sha != envelope.base_sha
            or snapshot.worktree_path != envelope.worktree_path
        ):
            raise ValueError("workspace identity changed since the task started")
        return ScopedApproval(
            id=f"approval-{uuid.uuid4().hex}",
            task_id=task_id,
            capability_id=capability_id,
            base_sha=snapshot.base_sha,
            worktree_path=snapshot.worktree_path,
            read_paths=capability.read_paths,
            write_paths=capability.write_paths,
            side_effects=capability.side_effects,
            network=capability.network,
            listener=capability.listener,
            environment=capability.environment,
            max_uses=max_uses,
            expires_at=datetime.now(UTC) + timedelta(minutes=valid_for_minutes),
        )

    def grant_approval(self, approval: ScopedApproval) -> TaskRecord:
        current = self.plan_approval(
            approval.task_id,
            approval.capability_id,
            max_uses=approval.max_uses,
        )
        if (
            current.base_sha != approval.base_sha
            or current.worktree_path != approval.worktree_path
            or current.read_paths != approval.read_paths
            or current.write_paths != approval.write_paths
            or current.side_effects != approval.side_effects
            or current.network is not approval.network
            or current.listener != approval.listener
            or current.environment is not approval.environment
        ):
            raise ValueError("approval scope changed before confirmation")
        return self.store.append_event(
            approval.task_id,
            "approval.granted",
            _approval_payload(approval),
        )

    def run_capability(self, task_id: str, capability_id: str) -> CommandResult:
        capabilities = self._capabilities()
        authorization = CapabilityGateway(
            store=self.store,
            capabilities=capabilities,
            policy=ProjectPolicy(),
            workspace_probe=self.workspace.snapshot,
        ).authorize(
            task_id,
            capability_id,
            approvals=self._recorded_approvals(task_id, capability_id),
        )
        return Executor(
            store=self.store,
            artifact_root=self.artifact_root,
            workspace_probe=self.workspace.snapshot,
            limits=ExecutionLimits(),
        ).execute(authorization)

    def _config(self) -> dict[str, Any]:
        path = self.root / ".harness/config.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        validator_for("config").validate(value)
        if not isinstance(value, dict):
            raise ValueError("Harness config root must be an object")
        return cast(dict[str, Any], value)

    def _capabilities(self) -> tuple[Capability, ...]:
        path = self.root / ".harness/capabilities.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("capabilities root must be an object")
        return load_capabilities(cast(dict[str, Any], value))

    def _capability(self, capability_id: str) -> Capability:
        for capability in self._capabilities():
            if capability.id == capability_id:
                return capability
        raise ValueError(f"unknown capability: {capability_id}")

    def _recorded_approvals(
        self, task_id: str, capability_id: str
    ) -> tuple[ScopedApproval, ...]:
        approvals: list[ScopedApproval] = []
        for event in self.store.load(task_id).events:
            if (
                event.type != "approval.granted"
                or event.data.get("capability_id") != capability_id
            ):
                continue
            approvals.append(_approval_from_payload(event.data))
        return tuple(approvals)


def _task_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"task-{timestamp}-{uuid.uuid4().hex[:8]}"


def _approval_payload(approval: ScopedApproval) -> dict[str, Any]:
    return {
        "approval_id": approval.id,
        "task_id": approval.task_id,
        "capability_id": approval.capability_id,
        "base_sha": approval.base_sha,
        "worktree_path": str(approval.worktree_path),
        "read_paths": list(approval.read_paths),
        "write_paths": list(approval.write_paths),
        "side_effects": [item.value for item in approval.side_effects],
        "network": approval.network.value,
        "listener": approval.listener,
        "environment": approval.environment.value,
        "max_uses": approval.max_uses,
        "expires_at": approval.expires_at.isoformat(),
    }


def _approval_from_payload(value: dict[str, Any]) -> ScopedApproval:
    return ScopedApproval(
        id=cast(str, value["approval_id"]),
        task_id=cast(str, value["task_id"]),
        capability_id=cast(str, value["capability_id"]),
        base_sha=cast(str, value["base_sha"]),
        worktree_path=Path(cast(str, value["worktree_path"])),
        read_paths=tuple(cast(list[str], value["read_paths"])),
        write_paths=tuple(cast(list[str], value["write_paths"])),
        side_effects=tuple(
            SideEffect(item) for item in cast(list[str], value["side_effects"])
        ),
        network=NetworkAccess(cast(str, value["network"])),
        listener=cast(bool, value["listener"]),
        environment=ExecutionEnvironment(cast(str, value["environment"])),
        max_uses=cast(int, value["max_uses"]),
        expires_at=datetime.fromisoformat(cast(str, value["expires_at"])),
    )


__all__ = ["TaskService"]
