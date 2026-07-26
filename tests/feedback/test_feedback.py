import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from adaptive_harness.feedback import (
    AnalysisPolicy,
    EffectObservation,
    FailureKind,
    FailureSignal,
    FeedbackConfiguration,
    FeedbackEpisode,
    FeedbackMode,
    FeedbackStore,
    Maturity,
    RecommendationEngine,
    RecommendationTarget,
    classify_failure,
)
from adaptive_harness.init import Initializer
from tests.storage_support import committed_storage_locator


def _store(
    tmp_path: Path,
    *,
    mode: FeedbackMode,
    **kwargs: Any,
) -> FeedbackStore:
    locator = committed_storage_locator(
        tmp_path / "project", tmp_path / "data"
    )
    return FeedbackStore(
        None,
        None,
        project_data=locator.location().project_data,
        mode=mode,
        storage_locator=locator,
        **kwargs,
    )


def _episode(identifier: str = "episode-1") -> FeedbackEpisode:
    return FeedbackEpisode(
        episode_id=identifier,
        task_id=f"task-{identifier}",
        created_at="2026-07-16T00:00:00Z",
        module_ids=("tdd-guidance",),
        template_ids=(),
        capability_ids=("project-tests",),
        approval_event_ids=("approval-1",),
        command_event_ids=("command-1",),
        failure=None,
        coverage_percent=91.0,
        diff_paths=("src/example.py",),
        result="completed",
        duration_ms=1200,
        token_usage=500,
        cost=0.02,
    )


def test_failure_taxonomy_uses_earliest_observable_primary_cause() -> None:
    classification = classify_failure(
        (
            FailureSignal(FailureKind.COMMAND_FAILURE, 3, "event:3"),
            FailureSignal(FailureKind.ENVIRONMENT_FAILURE, 1, "event:1"),
            FailureSignal(FailureKind.VERIFICATION_GAP, 4, "event:4"),
        )
    )

    assert classification is not None
    assert classification.primary is FailureKind.ENVIRONMENT_FAILURE
    assert classification.contributing == (
        FailureKind.COMMAND_FAILURE,
        FailureKind.VERIFICATION_GAP,
    )


def test_off_mode_creates_no_episode_or_directory(tmp_path: Path) -> None:
    store = _store(
        tmp_path, mode=FeedbackMode.OFF, analyzer=lambda _: {}
    )

    assert store.record(_episode()) is None
    assert not store.root.exists()


def test_minimal_mode_never_calls_analyzer_and_can_omit_usage(tmp_path: Path) -> None:
    calls = 0

    def analyzer(_: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"summary": "should not run"}

    store = _store(
        tmp_path,
        mode=FeedbackMode.MINIMAL,
        analysis_policy=AnalysisPolicy.AFTER_EACH_TASK,
        include_token_usage=False,
        analyzer=analyzer,
    )

    path = store.record(_episode())

    assert path is not None
    assert calls == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["token_usage"] is None
    assert document["cost"] is None
    assert "analysis" not in document


def test_feedback_writes_use_project_data_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries: list[Path] = []

    @contextmanager
    def tracked_lock(
        project_data: Path,
        storage_locator: object,
        *,
        force_user_data: bool,
    ) -> Any:
        assert storage_locator is not None
        assert force_user_data is False
        entries.append(project_data)
        yield

    monkeypatch.setattr(
        "adaptive_harness.feedback.store.bound_project_data_lock", tracked_lock
    )
    store = _store(tmp_path, mode=FeedbackMode.MINIMAL)

    store.record(_episode())

    assert entries == [store.root.parent]


def test_feedback_writer_requires_clone_binding(tmp_path: Path) -> None:
    store = FeedbackStore(
        tmp_path, "repo-1", mode=FeedbackMode.MINIMAL
    )

    with pytest.raises(ValueError, match="storage locator is required"):
        store.list()
    with pytest.raises(ValueError, match="storage locator is required"):
        store.record(_episode())


def test_research_mode_calls_only_explicit_bounded_analyzer(tmp_path: Path) -> None:
    calls = 0

    def analyzer(document: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        assert "command_event_ids" in document
        return {"summary": "local analysis", "confidence": 0.7}

    store = _store(
        tmp_path,
        mode=FeedbackMode.RESEARCH,
        analysis_policy=AnalysisPolicy.AFTER_EACH_TASK,
        analyzer=analyzer,
    )

    path = store.record(_episode())

    assert path is not None
    assert calls == 1
    assert json.loads(path.read_text(encoding="utf-8"))["analysis"] == {
        "summary": "local analysis",
        "confidence": 0.7,
    }


def _observation(index: int, beneficial: bool = True) -> EffectObservation:
    return EffectObservation(
        task_id=f"task-{index}",
        target=RecommendationTarget.MODULE,
        target_id="tdd-guidance",
        failure_kind=FailureKind.PRODUCT_REGRESSION,
        beneficial=beneficial,
        high_impact=False,
        evidence_ref=f"episode:{index}",
        overhead_ms=100 + index,
    )


def test_recommendation_maturity_counterexamples_and_cooldown() -> None:
    engine = RecommendationEngine()
    now = datetime(2026, 7, 16, tzinfo=UTC)

    observation = engine.evaluate((_observation(1),), now=now)[0]
    candidate = engine.evaluate((_observation(1), _observation(2)), now=now)[0]
    recommendation = engine.evaluate(
        (_observation(1), _observation(2), _observation(3, False), _observation(4)),
        now=now,
    )[0]

    assert observation.maturity is Maturity.OBSERVATION
    assert candidate.maturity is Maturity.CANDIDATE
    assert recommendation.maturity is Maturity.RECOMMENDATION
    assert recommendation.counterexamples == ("episode:3",)
    assert recommendation.rollback
    engine.reject(recommendation, now=now)
    assert engine.evaluate((_observation(1),), now=now + timedelta(days=1)) == ()
    assert engine.evaluate(
        (_observation(1),), now=now + timedelta(days=31)
    )


def test_accepting_module_recommendation_can_only_start_trial(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="generic"))
    recommendation = RecommendationEngine().evaluate(
        (_observation(1), _observation(2), _observation(3))
    )[0]

    plan = FeedbackConfiguration(root).accept_recommendation(recommendation)

    assert plan.operation == "trial"
    assert ".harness/modules.lock.json" in plan.diff()


def test_feedback_mode_change_is_reviewed_and_transactional(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="generic"))
    configuration = FeedbackConfiguration(root)

    plan = configuration.plan_mode(
        FeedbackMode.RESEARCH,
        analysis_policy=AnalysisPolicy.ON_DEMAND,
    )

    assert configuration.current()["mode"] == "minimal"
    assert '"research"' in plan.diff()
    configuration.apply(plan)
    assert configuration.current()["mode"] == "research"
