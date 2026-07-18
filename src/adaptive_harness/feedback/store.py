"""Zero-upload local feedback episode persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from adaptive_harness.feedback.model import (
    AnalysisPolicy,
    FeedbackEpisode,
    FeedbackMode,
)

Analyzer = Callable[[dict[str, Any]], dict[str, Any]]


class FeedbackStore:
    """Persist bounded structured events without raw logs or model thoughts."""

    def __init__(
        self,
        data_root: Path,
        repository_id: str,
        *,
        mode: FeedbackMode,
        analysis_policy: AnalysisPolicy = AnalysisPolicy.ON_DEMAND,
        include_token_usage: bool = True,
        analyzer: Analyzer | None = None,
    ) -> None:
        if not repository_id or "/" in repository_id or ".." in repository_id:
            raise ValueError("repository id is unsafe")
        self.root = Path(data_root).resolve() / "projects" / repository_id / "episodes"
        self.mode = mode
        self.analysis_policy = analysis_policy
        self.include_token_usage = include_token_usage
        self._analyzer = analyzer

    def record(self, episode: FeedbackEpisode) -> Path | None:
        if self.mode is FeedbackMode.OFF:
            return None
        document = episode.to_dict(include_token_usage=self.include_token_usage)
        if (
            self.mode is FeedbackMode.RESEARCH
            and self.analysis_policy is AnalysisPolicy.AFTER_EACH_TASK
        ):
            document["analysis"] = self._analyze(document)
        path = self.root / f"{episode.episode_id}.json"
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_json(path, document)
        return path

    def list(self) -> tuple[dict[str, Any], ...]:
        if not self.root.is_dir():
            return ()
        documents: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                documents.append(value)
        return tuple(documents)

    def analyze(self, episode_id: str) -> dict[str, Any]:
        if self.mode is not FeedbackMode.RESEARCH:
            raise ValueError("analysis is available only in research mode")
        path = self.root / f"{episode_id}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("episode root must be an object")
        analysis = self._analyze(value)
        value["analysis"] = analysis
        _atomic_json(path, value)
        return analysis

    def _analyze(self, document: dict[str, Any]) -> dict[str, Any]:
        if self._analyzer is None:
            raise ValueError("research analysis requires an explicit analyzer")
        analysis = self._analyzer(document)
        allowed = {"summary", "attribution", "confidence"}
        if not isinstance(analysis, dict) or not set(analysis).issubset(allowed):
            raise ValueError("analyzer returned unsupported fields")
        return analysis


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    content = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__ = ["Analyzer", "FeedbackStore"]
