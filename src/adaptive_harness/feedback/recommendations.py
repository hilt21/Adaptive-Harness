"""Evidence maturity and tightly scoped local recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from adaptive_harness.feedback.model import FailureKind


class Maturity(StrEnum):
    OBSERVATION = "observation"
    CANDIDATE = "candidate"
    RECOMMENDATION = "recommendation"


class RecommendationTarget(StrEnum):
    MODULE = "module"
    TEMPLATE = "template"
    FEEDBACK_POLICY = "feedback_policy"


@dataclass(frozen=True, slots=True)
class EffectObservation:
    task_id: str
    target: RecommendationTarget
    target_id: str
    failure_kind: FailureKind
    beneficial: bool
    high_impact: bool
    evidence_ref: str
    overhead_ms: int

    @property
    def key(self) -> str:
        return f"{self.target.value}:{self.target_id}:{self.failure_kind.value}"


@dataclass(frozen=True, slots=True)
class Recommendation:
    key: str
    maturity: Maturity
    target: RecommendationTarget
    target_id: str
    evidence_refs: tuple[str, ...]
    counterexamples: tuple[str, ...]
    expected_overhead_ms: int
    improvement_metric: str
    confidence: float
    rollback: str
    matching_tasks: int


class RecommendationEngine:
    """Promote repeated evidence and honor rejection cooldowns."""

    def __init__(self) -> None:
        self._cooldowns: dict[str, datetime] = {}

    def evaluate(
        self,
        observations: tuple[EffectObservation, ...],
        *,
        now: datetime | None = None,
    ) -> tuple[Recommendation, ...]:
        current = now or datetime.now(UTC)
        groups: dict[str, list[EffectObservation]] = {}
        for observation in observations:
            groups.setdefault(observation.key, []).append(observation)
        recommendations: list[Recommendation] = []
        for key, items in sorted(groups.items()):
            cooldown = self._cooldowns.get(key)
            if cooldown is not None and current < cooldown:
                continue
            unique_tasks = {item.task_id for item in items}
            beneficial = [item for item in items if item.beneficial]
            if len(unique_tasks) >= 3 and len(beneficial) > len(items) / 2:
                maturity = Maturity.RECOMMENDATION
            elif len(unique_tasks) >= 2 or any(item.high_impact for item in items):
                maturity = Maturity.CANDIDATE
            else:
                maturity = Maturity.OBSERVATION
            first = items[0]
            ratio = len(beneficial) / len(items)
            recommendations.append(
                Recommendation(
                    key=key,
                    maturity=maturity,
                    target=first.target,
                    target_id=first.target_id,
                    evidence_refs=tuple(item.evidence_ref for item in beneficial),
                    counterexamples=tuple(
                        item.evidence_ref for item in items if not item.beneficial
                    ),
                    expected_overhead_ms=round(
                        sum(item.overhead_ms for item in items) / len(items)
                    ),
                    improvement_metric="matching_task_success_rate",
                    confidence=min(0.95, 0.35 + len(unique_tasks) * 0.12 + ratio * 0.2),
                    rollback=f"restore prior {first.target.value} configuration",
                    matching_tasks=len(unique_tasks),
                )
            )
        return tuple(recommendations)

    def reject(
        self,
        recommendation: Recommendation,
        *,
        now: datetime | None = None,
        cooldown_days: int = 30,
    ) -> None:
        if cooldown_days < 1:
            raise ValueError("cooldown must be at least one day")
        self._cooldowns[recommendation.key] = (now or datetime.now(UTC)) + timedelta(
            days=cooldown_days
        )


__all__ = [
    "EffectObservation",
    "Maturity",
    "Recommendation",
    "RecommendationEngine",
    "RecommendationTarget",
]
