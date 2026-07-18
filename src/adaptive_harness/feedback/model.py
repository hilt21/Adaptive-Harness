"""Structured local feedback facts and failure classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class FeedbackMode(StrEnum):
    OFF = "off"
    MINIMAL = "minimal"
    RESEARCH = "research"


class AnalysisPolicy(StrEnum):
    ON_DEMAND = "on_demand"
    SCHEDULED = "scheduled"
    AFTER_EACH_TASK = "after_each_task"


class HumanRating(StrEnum):
    HELPFUL = "helpful"
    NO_CLEAR_IMPACT = "no_clear_impact"
    INTERFERING = "interfering"


class FailureKind(StrEnum):
    WORKSPACE_MISMATCH = "workspace_mismatch"
    CAPABILITY_DENIED = "capability_denied"
    ENVIRONMENT_FAILURE = "environment_failure"
    COMMAND_FAILURE = "command_failure"
    VERIFICATION_GAP = "verification_gap"
    PRODUCT_REGRESSION = "product_regression"
    SCOPE_DEVIATION = "scope_deviation"
    MODEL_REASONING_FAILURE = "model_reasoning_failure"
    FLAKY_OR_NONDETERMINISTIC = "flaky_or_nondeterministic"
    USER_OR_REQUIREMENT_CHANGE = "user_or_requirement_change"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class FailureSignal:
    kind: FailureKind
    sequence: int
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class FailureClassification:
    primary: FailureKind
    contributing: tuple[FailureKind, ...]
    evidence_refs: tuple[str, ...]


def classify_failure(
    signals: tuple[FailureSignal, ...],
) -> FailureClassification | None:
    """Use the earliest observable cause and retain later contributors."""
    if not signals:
        return None
    ordered = sorted(signals, key=lambda item: item.sequence)
    primary = ordered[0].kind
    contributing: list[FailureKind] = []
    for signal in ordered[1:]:
        if signal.kind != primary and signal.kind not in contributing:
            contributing.append(signal.kind)
    return FailureClassification(
        primary,
        tuple(contributing),
        tuple(item.evidence_ref for item in ordered),
    )


@dataclass(frozen=True, slots=True)
class FeedbackEpisode:
    episode_id: str
    task_id: str
    created_at: str
    module_ids: tuple[str, ...]
    template_ids: tuple[str, ...]
    capability_ids: tuple[str, ...]
    approval_event_ids: tuple[str, ...]
    command_event_ids: tuple[str, ...]
    failure: FailureClassification | None
    coverage_percent: float | None
    diff_paths: tuple[str, ...]
    result: str
    duration_ms: int
    token_usage: int | None = None
    cost: float | None = None
    human_rating: HumanRating | None = None
    intervention_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.episode_id or not self.task_id:
            raise ValueError("episode and task ids are required")
        if self.duration_ms < 0 or (
            self.token_usage is not None and self.token_usage < 0
        ):
            raise ValueError("episode measurements cannot be negative")
        if self.cost is not None and self.cost < 0:
            raise ValueError("episode cost cannot be negative")

    def to_dict(self, *, include_token_usage: bool) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "episode_id": self.episode_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "module_ids": list(self.module_ids),
            "template_ids": list(self.template_ids),
            "capability_ids": list(self.capability_ids),
            "approval_event_ids": list(self.approval_event_ids),
            "command_event_ids": list(self.command_event_ids),
            "failure": (
                {
                    "primary": self.failure.primary.value,
                    "contributing": [
                        item.value for item in self.failure.contributing
                    ],
                    "evidence_refs": list(self.failure.evidence_refs),
                }
                if self.failure is not None
                else None
            ),
            "coverage_percent": self.coverage_percent,
            "diff_paths": list(self.diff_paths),
            "result": self.result,
            "duration_ms": self.duration_ms,
            "token_usage": self.token_usage if include_token_usage else None,
            "cost": self.cost if include_token_usage else None,
            "human_rating": (
                self.human_rating.value if self.human_rating is not None else None
            ),
            "intervention_reasons": list(self.intervention_reasons),
        }


__all__ = [
    "AnalysisPolicy",
    "FailureClassification",
    "FailureKind",
    "FailureSignal",
    "FeedbackEpisode",
    "FeedbackMode",
    "HumanRating",
    "classify_failure",
]
