"""Adapter contract shared by supported coding clients."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class IntegrationMode(StrEnum):
    """Strength of the integration that has actually been verified."""

    OBSERVE = "observe"
    ENFORCED = "enforced"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ClientDetection:
    client_id: str
    detected: bool
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModelDetection:
    model_id: str | None
    evidence: str | None


@dataclass(frozen=True, slots=True)
class CapabilityProbe:
    mode: IntegrationMode
    can_intercept: bool
    verified_e2e: bool
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.mode is IntegrationMode.ENFORCED and not (
            self.can_intercept and self.verified_e2e
        ):
            raise ValueError("enforced mode requires interception and verified E2E")


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    state: HealthState
    mode: IntegrationMode
    installed: bool
    message: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, Any]
    mutating: bool


@dataclass(frozen=True, slots=True)
class ToolDecision:
    allowed: bool
    mode: IntegrationMode
    routed_via_gateway: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ToolObservation:
    call_id: str
    name: str
    succeeded: bool
    summary: str
    mode: IntegrationMode


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    capability_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ApprovalResponse:
    approved: bool
    reason: str


ApprovalCallback = Callable[[ApprovalRequest], ApprovalResponse]
ObservationCallback = Callable[[ToolObservation], None]


class ApprovalUnavailableError(RuntimeError):
    """Raised when the host client has no approval callback."""


class ClientAdapter:
    """Conservative base implementation of the PRD adapter contract."""

    client_id = "generic"
    projection_path: str | None = None
    detection_environment: tuple[str, ...] = ()
    detection_directories: tuple[str, ...] = ()
    model_environment: tuple[str, ...] = ()

    def __init__(
        self,
        root: Path,
        *,
        environment: Mapping[str, str] | None = None,
        approval_callback: ApprovalCallback | None = None,
        observation_callback: ObservationCallback | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.environment = dict(environment or {})
        self._approval_callback = approval_callback
        self._observation_callback = observation_callback

    def detect_client(self) -> ClientDetection:
        evidence = tuple(
            f"environment:{name}"
            for name in self.detection_environment
            if self.environment.get(name)
        )
        evidence += tuple(
            f"directory:{name}"
            for name in self.detection_directories
            if (self.root / name).is_dir()
        )
        return ClientDetection(
            client_id=self.client_id,
            detected=self.client_id == "generic" or bool(evidence),
            evidence=evidence
            or (("explicit:generic",) if self.client_id == "generic" else ()),
        )

    def detect_model(self) -> ModelDetection:
        for name in self.model_environment:
            value = self.environment.get(name)
            if value:
                return ModelDetection(value, f"environment:{name}")
        return ModelDetection(None, None)

    def capability_probe(self) -> CapabilityProbe:
        return CapabilityProbe(
            mode=IntegrationMode.OBSERVE,
            can_intercept=False,
            verified_e2e=False,
            evidence=("prompt projection cannot intercept tool calls",),
        )

    def install_integration(self, existing: str | None) -> str | None:
        return existing

    def uninstall_integration(self, existing: str | None) -> str | None:
        return existing

    def before_tool_call(self, call: ToolCall) -> ToolDecision:
        return ToolDecision(
            allowed=True,
            mode=IntegrationMode.OBSERVE,
            routed_via_gateway=False,
            reason=(
                "observe mode records the call but cannot block it"
                if call.mutating
                else "read-only call observed"
            ),
        )

    def after_tool_call(
        self, call: ToolCall, *, succeeded: bool, summary: str
    ) -> ToolObservation:
        return ToolObservation(
            call_id=call.call_id,
            name=call.name,
            succeeded=succeeded,
            summary=summary,
            mode=IntegrationMode.OBSERVE,
        )

    def request_user_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        if self._approval_callback is None:
            raise ApprovalUnavailableError(
                f"{self.client_id} adapter has no user approval callback"
            )
        return self._approval_callback(request)

    def deliver_observation(self, observation: ToolObservation) -> None:
        if self._observation_callback is not None:
            self._observation_callback(observation)

    def health_check(self) -> AdapterHealth:
        return AdapterHealth(
            state=HealthState.HEALTHY,
            mode=IntegrationMode.OBSERVE,
            installed=True,
            message="generic observe adapter requires no client integration",
        )


__all__ = [
    "AdapterHealth",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalUnavailableError",
    "CapabilityProbe",
    "ClientAdapter",
    "ClientDetection",
    "HealthState",
    "IntegrationMode",
    "ModelDetection",
    "ToolCall",
    "ToolDecision",
    "ToolObservation",
]
