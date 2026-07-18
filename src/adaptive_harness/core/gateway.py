"""Typed capability authorization and scoped approval enforcement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Self

from adaptive_harness.core.envelope import TaskEnvelope
from adaptive_harness.core.store import TaskRecord, TaskStore


class CapabilityInvariantError(ValueError):
    """Raised when a capability or approval declaration is unsafe."""


class CapabilityDeniedError(RuntimeError):
    """Raised when the Gateway refuses an authorization request."""


class NetworkAccess(StrEnum):
    NONE = "none"
    OUTBOUND = "outbound"


class SideEffect(StrEnum):
    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    NETWORK = "network"
    LISTENER = "listener"
    DEPENDENCY_CHANGE = "dependency_change"
    MIGRATION = "migration"
    CREDENTIALS = "credentials"
    PRODUCTION = "production"
    IRREVERSIBLE = "irreversible"


class Reversibility(StrEnum):
    REVERSIBLE = "reversible"
    CONDITIONAL = "conditional"
    IRREVERSIBLE = "irreversible"


class ExecutionEnvironment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ApprovalPolicy(StrEnum):
    AUTO = "auto"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class Capability:
    """A complete, typed declaration of one executable operation."""

    id: str
    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: int
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    network: NetworkAccess
    listener: bool
    side_effects: tuple[SideEffect, ...]
    reversibility: Reversibility
    environment: ExecutionEnvironment
    approval_policy: ApprovalPolicy
    max_executions: int
    stop_on_exit_codes: tuple[int, ...]

    def __post_init__(self) -> None:
        if not _is_identifier(self.id):
            raise CapabilityInvariantError("capability id is invalid")
        if not isinstance(self.argv, tuple) or not self.argv or not all(
            isinstance(item, str) and item for item in self.argv
        ):
            raise CapabilityInvariantError("argv must be a non-empty string tuple")
        _validate_relative_path("cwd", self.cwd)
        _validate_positive_integer("timeout_seconds", self.timeout_seconds)
        _validate_paths("read_paths", self.read_paths)
        _validate_paths("write_paths", self.write_paths)
        if not isinstance(self.network, NetworkAccess):
            raise CapabilityInvariantError("network is not a known access mode")
        if type(self.listener) is not bool:
            raise CapabilityInvariantError("listener must be a boolean")
        if not isinstance(self.side_effects, tuple) or not all(
            isinstance(item, SideEffect) for item in self.side_effects
        ):
            raise CapabilityInvariantError("side_effects contains an unknown value")
        if len(set(self.side_effects)) != len(self.side_effects):
            raise CapabilityInvariantError("side_effects contains duplicates")
        if not isinstance(self.reversibility, Reversibility):
            raise CapabilityInvariantError("reversibility is invalid")
        if not isinstance(self.environment, ExecutionEnvironment):
            raise CapabilityInvariantError("environment is invalid")
        if not isinstance(self.approval_policy, ApprovalPolicy):
            raise CapabilityInvariantError("approval_policy is invalid")
        _validate_positive_integer("max_executions", self.max_executions)
        if not isinstance(self.stop_on_exit_codes, tuple) or not all(
            type(item) is int for item in self.stop_on_exit_codes
        ):
            raise CapabilityInvariantError(
                "stop_on_exit_codes must be an integer tuple"
            )
        if (
            self.network is NetworkAccess.OUTBOUND
            and SideEffect.NETWORK not in self.side_effects
        ):
            raise CapabilityInvariantError(
                "outbound network must declare the network side effect"
            )
        if self.listener and SideEffect.LISTENER not in self.side_effects:
            raise CapabilityInvariantError(
                "listener access must declare the listener side effect"
            )
        if self.write_paths and SideEffect.FILESYSTEM_WRITE not in self.side_effects:
            raise CapabilityInvariantError(
                "write_paths must declare the filesystem_write side effect"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the stable capabilities.json representation."""
        return {
            "id": self.id,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "timeout_seconds": self.timeout_seconds,
            "read_paths": list(self.read_paths),
            "write_paths": list(self.write_paths),
            "network": self.network.value,
            "listener": self.listener,
            "side_effects": [item.value for item in self.side_effects],
            "reversibility": self.reversibility.value,
            "environment": self.environment.value,
            "approval_policy": self.approval_policy.value,
            "max_executions": self.max_executions,
            "stop_condition": {"exit_codes": list(self.stop_on_exit_codes)},
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        """Load one strict capabilities.json declaration."""
        expected = {
            "id",
            "argv",
            "cwd",
            "timeout_seconds",
            "read_paths",
            "write_paths",
            "network",
            "listener",
            "side_effects",
            "reversibility",
            "environment",
            "approval_policy",
            "max_executions",
            "stop_condition",
        }
        _require_exact_fields(value, expected, "capability")
        stop_condition = value["stop_condition"]
        if not isinstance(stop_condition, dict):
            raise CapabilityInvariantError("stop_condition must be an object")
        _require_exact_fields(
            stop_condition, {"exit_codes"}, "stop_condition"
        )
        try:
            return cls(
                id=value["id"],
                argv=tuple(value["argv"]),
                cwd=value["cwd"],
                timeout_seconds=value["timeout_seconds"],
                read_paths=tuple(value["read_paths"]),
                write_paths=tuple(value["write_paths"]),
                network=NetworkAccess(value["network"]),
                listener=value["listener"],
                side_effects=tuple(
                    SideEffect(item) for item in value["side_effects"]
                ),
                reversibility=Reversibility(value["reversibility"]),
                environment=ExecutionEnvironment(value["environment"]),
                approval_policy=ApprovalPolicy(value["approval_policy"]),
                max_executions=value["max_executions"],
                stop_on_exit_codes=tuple(stop_condition["exit_codes"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, CapabilityInvariantError):
                raise
            raise CapabilityInvariantError(
                "capability declaration is invalid"
            ) from error


@dataclass(frozen=True, slots=True)
class ProjectPolicy:
    """Finite policy inputs evaluated below non-overridable kernel invariants."""

    denied_capabilities: tuple[str, ...] = ()
    allowed_network: tuple[NetworkAccess, ...] = (NetworkAccess.NONE,)
    allow_listener: bool = False
    allowed_environments: tuple[ExecutionEnvironment, ...] = (
        ExecutionEnvironment.LOCAL,
        ExecutionEnvironment.TEST,
    )
    auto_approved_side_effects: tuple[SideEffect, ...] = (
        SideEffect.FILESYSTEM_READ,
    )

    def __post_init__(self) -> None:
        if not all(_is_identifier(item) for item in self.denied_capabilities):
            raise CapabilityInvariantError("denied_capabilities is invalid")
        if not self.allowed_network or not all(
            isinstance(item, NetworkAccess) for item in self.allowed_network
        ):
            raise CapabilityInvariantError("allowed_network is invalid")
        if type(self.allow_listener) is not bool:
            raise CapabilityInvariantError("allow_listener must be a boolean")
        if not self.allowed_environments or not all(
            isinstance(item, ExecutionEnvironment)
            for item in self.allowed_environments
        ):
            raise CapabilityInvariantError("allowed_environments is invalid")
        if not all(
            isinstance(item, SideEffect) for item in self.auto_approved_side_effects
        ):
            raise CapabilityInvariantError("auto_approved_side_effects is invalid")


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    repository_id: str
    base_sha: str
    worktree_path: Path


@dataclass(frozen=True, slots=True)
class ScopedApproval:
    """A human approval bound to one exact operation scope."""

    id: str
    task_id: str
    capability_id: str
    base_sha: str
    worktree_path: Path
    read_paths: tuple[str, ...]
    write_paths: tuple[str, ...]
    side_effects: tuple[SideEffect, ...]
    network: NetworkAccess
    listener: bool
    environment: ExecutionEnvironment
    max_uses: int
    expires_at: datetime

    def __post_init__(self) -> None:
        for name, value in (
            ("approval id", self.id),
            ("task id", self.task_id),
            ("capability id", self.capability_id),
        ):
            if not _is_identifier(value):
                raise CapabilityInvariantError(f"{name} is invalid")
        if not self.worktree_path.is_absolute():
            raise CapabilityInvariantError("approval worktree_path must be absolute")
        _validate_paths("approval read_paths", self.read_paths)
        _validate_paths("approval write_paths", self.write_paths)
        if not all(isinstance(item, SideEffect) for item in self.side_effects):
            raise CapabilityInvariantError("approval side_effects is invalid")
        if not isinstance(self.network, NetworkAccess):
            raise CapabilityInvariantError("approval network is invalid")
        if type(self.listener) is not bool:
            raise CapabilityInvariantError("approval listener must be a boolean")
        if not isinstance(self.environment, ExecutionEnvironment):
            raise CapabilityInvariantError("approval environment is invalid")
        _validate_positive_integer("approval max_uses", self.max_uses)
        if self.expires_at.tzinfo is None:
            raise CapabilityInvariantError("approval expires_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AuthorizedCommand:
    task_id: str
    capability_id: str
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    approval_id: str | None
    authorization_event_sequence: int
    stop_on_exit_codes: tuple[int, ...]
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()
    network: NetworkAccess = NetworkAccess.NONE
    listener: bool = False


class CapabilityGateway:
    """Authorize declared commands and persist the authorization fact."""

    def __init__(
        self,
        *,
        store: TaskStore,
        capabilities: tuple[Capability, ...],
        policy: ProjectPolicy,
        workspace_probe: Callable[[], WorkspaceSnapshot],
    ) -> None:
        if len({item.id for item in capabilities}) != len(capabilities):
            raise CapabilityInvariantError("capability ids must be unique")
        self.store = store
        self.workspace_probe = workspace_probe
        self._capabilities = {item.id: item for item in capabilities}
        self._policy = policy

    def authorize(
        self,
        task_id: str,
        capability_id: str,
        *,
        approvals: tuple[ScopedApproval, ...] = (),
    ) -> AuthorizedCommand:
        """Authorize one command after revalidating all bound external facts."""
        record = self.store.load(task_id)
        envelope = record.current_envelope
        try:
            capability = self._capabilities[capability_id]
        except KeyError as error:
            raise CapabilityDeniedError(
                f"unknown capability: {capability_id}"
            ) from error
        if capability_id not in envelope.requested_capabilities:
            raise CapabilityDeniedError(
                f"capability was not requested by the task: {capability_id}"
            )

        snapshot = self.workspace_probe()
        _validate_workspace(envelope, snapshot)
        _validate_write_scope(envelope, capability)
        self._evaluate_policy(capability)

        prior_executions = sum(
            event.type == "command.authorized"
            and event.data.get("capability_id") == capability.id
            for event in record.events
        )
        if prior_executions >= capability.max_executions:
            raise CapabilityDeniedError(
                f"capability execution limit reached: {capability.id}"
            )
        prior_attempts = sum(
            event.type == "command.started"
            and event.data.get("capability_id") == capability.id
            for event in record.events
        )
        if prior_attempts >= envelope.retry_budget + 1:
            raise CapabilityDeniedError(
                f"task retry budget reached for capability: {capability.id}"
            )
        max_commands = envelope.budget.get("max_commands")
        if max_commands is not None:
            total_authorizations = sum(
                event.type == "command.authorized" for event in record.events
            )
            if total_authorizations >= max_commands:
                raise CapabilityDeniedError("task command budget reached")

        approval_id: str | None = None
        if self._requires_approval(capability):
            approval = self._find_approval(
                record, envelope, capability, approvals, snapshot
            )
            approval_id = approval.id

        command_cwd = (snapshot.worktree_path / capability.cwd).resolve()
        updated = self.store.append_event(
            task_id,
            "command.authorized",
            {
                "capability_id": capability.id,
                "argv": list(capability.argv),
                "cwd": str(command_cwd),
                "read_paths": list(capability.read_paths),
                "write_paths": list(capability.write_paths),
                "network": capability.network.value,
                "listener": capability.listener,
                "side_effects": [item.value for item in capability.side_effects],
                "environment": capability.environment.value,
                "approval_id": approval_id,
                "timeout_seconds": capability.timeout_seconds,
                "stop_on_exit_codes": list(capability.stop_on_exit_codes),
                "envelope_revision": envelope.revision,
            },
        )
        return AuthorizedCommand(
            task_id=task_id,
            capability_id=capability.id,
            argv=capability.argv,
            cwd=command_cwd,
            timeout_seconds=capability.timeout_seconds,
            approval_id=approval_id,
            authorization_event_sequence=updated.events[-1].sequence,
            stop_on_exit_codes=capability.stop_on_exit_codes,
            read_paths=capability.read_paths,
            write_paths=capability.write_paths,
            network=capability.network,
            listener=capability.listener,
        )

    def _evaluate_policy(self, capability: Capability) -> None:
        if capability.id in self._policy.denied_capabilities:
            raise CapabilityDeniedError(
                f"capability is denied by project policy: {capability.id}"
            )
        if capability.approval_policy is ApprovalPolicy.DENY:
            raise CapabilityDeniedError(
                f"capability declaration denies execution: {capability.id}"
            )
        if capability.network not in self._policy.allowed_network:
            raise CapabilityDeniedError("network access is denied by project policy")
        if capability.listener and not self._policy.allow_listener:
            raise CapabilityDeniedError("listener access is denied by project policy")
        if capability.environment not in self._policy.allowed_environments:
            raise CapabilityDeniedError(
                "execution environment is denied by project policy"
            )

    def _requires_approval(self, capability: Capability) -> bool:
        kernel_high_risk = (
            capability.reversibility is Reversibility.IRREVERSIBLE
            or capability.environment is ExecutionEnvironment.PRODUCTION
            or SideEffect.PRODUCTION in capability.side_effects
            or SideEffect.CREDENTIALS in capability.side_effects
            or SideEffect.IRREVERSIBLE in capability.side_effects
        )
        policy_requires = not set(capability.side_effects) <= set(
            self._policy.auto_approved_side_effects
        )
        return (
            kernel_high_risk
            or policy_requires
            or capability.approval_policy is ApprovalPolicy.ASK
        )

    def _find_approval(
        self,
        record: TaskRecord,
        envelope: TaskEnvelope,
        capability: Capability,
        approvals: tuple[ScopedApproval, ...],
        snapshot: WorkspaceSnapshot,
    ) -> ScopedApproval:
        now = datetime.now(UTC)
        for approval in approvals:
            used = sum(
                event.type == "command.authorized"
                and event.data.get("approval_id") == approval.id
                for event in record.events
            )
            if (
                approval.task_id == envelope.task_id
                and approval.capability_id == capability.id
                and approval.base_sha == envelope.base_sha == snapshot.base_sha
                and approval.worktree_path == snapshot.worktree_path
                and set(approval.read_paths) == set(capability.read_paths)
                and set(approval.write_paths) == set(capability.write_paths)
                and set(approval.side_effects) == set(capability.side_effects)
                and approval.network is capability.network
                and approval.listener is capability.listener
                and approval.environment is capability.environment
                and approval.expires_at > now
                and used < approval.max_uses
            ):
                return approval
        raise CapabilityDeniedError(
            "operation requires a matching, unexpired scoped approval"
        )


def _validate_workspace(
    envelope: TaskEnvelope, snapshot: WorkspaceSnapshot
) -> None:
    if snapshot.repository_id != envelope.repository_id:
        raise CapabilityDeniedError("workspace repository identity changed")
    if snapshot.base_sha != envelope.base_sha:
        raise CapabilityDeniedError("workspace base SHA changed")
    if snapshot.worktree_path != envelope.worktree_path:
        raise CapabilityDeniedError("workspace worktree path changed")


def _validate_write_scope(envelope: TaskEnvelope, capability: Capability) -> None:
    allowed = tuple(PurePosixPath(path) for path in envelope.allowed_scope)
    for write_path in capability.write_paths:
        candidate = PurePosixPath(write_path)
        if not any(
            candidate == scope or candidate.is_relative_to(scope)
            for scope in allowed
        ):
            raise CapabilityDeniedError(
                f"write path is outside the task allowed scope: {write_path}"
            )


def _validate_paths(name: str, paths: tuple[str, ...]) -> None:
    if not isinstance(paths, tuple):
        raise CapabilityInvariantError(f"{name} must be a tuple")
    for path in paths:
        _validate_relative_path(name, path)
    if len(set(paths)) != len(paths):
        raise CapabilityInvariantError(f"{name} contains duplicates")


def _validate_relative_path(name: str, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise CapabilityInvariantError(f"{name} must not be empty")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise CapabilityInvariantError(
            f"{name} must be a repository-relative path without '..'"
        )


def _validate_positive_integer(name: str, value: int) -> None:
    if type(value) is not int or value < 1:
        raise CapabilityInvariantError(f"{name} must be a positive integer")


def _is_identifier(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return False
    return value[0].isalnum() and all(
        character.isalnum() or character in "._-" for character in value
    )


def _require_exact_fields(
    value: dict[str, Any], expected: set[str], description: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise CapabilityInvariantError(
            f"invalid {description} fields; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


__all__ = [
    "ApprovalPolicy",
    "AuthorizedCommand",
    "Capability",
    "CapabilityDeniedError",
    "CapabilityGateway",
    "CapabilityInvariantError",
    "ExecutionEnvironment",
    "NetworkAccess",
    "ProjectPolicy",
    "Reversibility",
    "ScopedApproval",
    "SideEffect",
    "WorkspaceSnapshot",
]
