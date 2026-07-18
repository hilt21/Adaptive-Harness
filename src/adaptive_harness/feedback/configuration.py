"""Reviewed feedback configuration changes and recommendation acceptance."""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jsonschema import ValidationError

from adaptive_harness.feedback.model import AnalysisPolicy, FeedbackMode
from adaptive_harness.feedback.recommendations import (
    Maturity,
    Recommendation,
    RecommendationTarget,
)
from adaptive_harness.init.transaction import (
    InitializationError,
    PlannedChange,
    RepositoryTransaction,
)
from adaptive_harness.modules import ModuleManager, ModulePlan
from adaptive_harness.schemas import validator_for


@dataclass(frozen=True, slots=True)
class FeedbackConfigPlan:
    root: Path
    operation: str
    changes: tuple[PlannedChange, ...]

    def diff(self) -> str:
        if not self.changes:
            return ""
        change = self.changes[0]
        return "".join(
            difflib.unified_diff(
                change.before.decode("utf-8").splitlines(keepends=True)
                if change.before is not None
                else [],
                change.after.decode("utf-8").splitlines(keepends=True),
                fromfile="a/.harness/config.json",
                tofile="b/.harness/config.json",
            )
        )


class FeedbackConfiguration:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._transaction = RepositoryTransaction(self.root)

    def current(self) -> dict[str, Any]:
        return cast(dict[str, Any], self._load()["feedback"])

    def plan_mode(
        self,
        mode: FeedbackMode,
        *,
        analysis_policy: AnalysisPolicy | None = None,
    ) -> FeedbackConfigPlan:
        document = self._load()
        feedback = cast(dict[str, Any], document["feedback"])
        feedback["mode"] = mode.value
        if analysis_policy is not None:
            feedback["analysis_policy"] = analysis_policy.value
        return self._plan("feedback-mode", document)

    def accept_recommendation(
        self, recommendation: Recommendation
    ) -> ModulePlan | FeedbackConfigPlan:
        if recommendation.maturity is not Maturity.RECOMMENDATION:
            raise ValueError("only mature recommendations can be accepted")
        if recommendation.target is RecommendationTarget.MODULE:
            return ModuleManager(self.root).plan_trial(recommendation.target_id)
        if recommendation.target is RecommendationTarget.FEEDBACK_POLICY:
            try:
                mode = FeedbackMode(recommendation.target_id)
            except ValueError as error:
                raise ValueError(
                    "unsupported feedback policy recommendation"
                ) from error
            return self.plan_mode(mode)
        raise ValueError(
            "template recommendations are advisory and cannot mutate configuration"
        )

    def apply(self, plan: FeedbackConfigPlan) -> None:
        if plan.root != self.root:
            raise ValueError("feedback plan belongs to another project")
        self._transaction.apply(plan.changes)

    def _load(self) -> dict[str, Any]:
        path = self.root / ".harness/config.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise InitializationError(
                f"Harness config is unreadable: {error}"
            ) from error
        try:
            validator_for("config").validate(value)
        except ValidationError as error:
            raise InitializationError(
                f"Harness config does not match its schema: {error.message}"
            ) from error
        if not isinstance(value, dict):
            raise InitializationError("Harness config root must be an object")
        return cast(dict[str, Any], value)

    def _plan(
        self, operation: str, document: dict[str, Any]
    ) -> FeedbackConfigPlan:
        path = self.root / ".harness/config.json"
        before = path.read_bytes()
        after = (
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        changes = (
            ()
            if before == after
            else (PlannedChange(".harness/config.json", before, after),)
        )
        return FeedbackConfigPlan(self.root, operation, changes)


__all__ = ["FeedbackConfigPlan", "FeedbackConfiguration"]
