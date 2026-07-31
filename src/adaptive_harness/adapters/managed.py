"""Stable managed blocks for Agent instruction projections."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_START = re.compile(
    r'^<!-- adaptive-harness:start version="(?P<version>[^"]+)" '
    r'hash="(?P<hash>[0-9a-f]{64})" -->$',
    re.MULTILINE,
)
_END = re.compile(r"^<!-- adaptive-harness:end -->$", re.MULTILINE)
_AGENT_BODY = (
    "Use Adaptive Harness for repository-changing tasks.\n"
    'Start or resume the task through `"$HOME/.local/bin/adp-harness" task`, '
    "request capability\n"
    "escalation through Harness, and run Harness verification before completion.\n"
    "Treat Harness task status and executor evidence as authoritative."
)


class ProjectionConflictError(ValueError):
    """Raised when managed markers are missing, duplicated, or misordered."""


@dataclass(frozen=True, slots=True)
class ProjectionStatus:
    present: bool
    valid: bool
    expected_hash: str
    actual_hash: str | None


@dataclass(frozen=True, slots=True)
class ManagedProjection:
    """One versioned content block owned by Adaptive Harness."""

    version: str
    body: str

    @classmethod
    def agent_instructions(cls) -> ManagedProjection:
        return cls(version="1", body=_AGENT_BODY)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    def render(self, existing: str | None) -> str:
        """Create or update the managed block without altering user content."""
        content = existing or ""
        span = _managed_span(content)
        block = self._block()
        if span is None:
            separator = "" if not content or content.endswith("\n") else "\n"
            return content + separator + block
        start, end = span
        return content[:start] + block + content[end:]

    def remove(self, existing: str) -> str:
        """Remove only the managed block, preserving all other bytes."""
        span = _managed_span(existing)
        if span is None:
            return existing
        start, end = span
        return existing[:start] + existing[end:]

    def inspect(self, content: str) -> ProjectionStatus:
        """Detect presence and content/hash drift."""
        span = _managed_span(content)
        if span is None:
            return ProjectionStatus(
                present=False,
                valid=False,
                expected_hash=self.content_hash,
                actual_hash=None,
            )
        start, end = span
        block = content[start:end]
        match = _START.search(block)
        if match is None:
            raise ProjectionConflictError("managed start marker is missing")
        body_start = match.end() + 1
        end_match = _END.search(block)
        if end_match is None:
            raise ProjectionConflictError("managed end marker is missing")
        body_end = end_match.start()
        if body_end > body_start and block[body_end - 1] == "\n":
            body_end -= 1
        actual_body = block[body_start:body_end]
        actual_hash = hashlib.sha256(actual_body.encode("utf-8")).hexdigest()
        marker_hash = match.group("hash")
        valid = (
            match.group("version") == self.version
            and marker_hash == actual_hash == self.content_hash
            and actual_body == self.body
        )
        return ProjectionStatus(
            present=True,
            valid=valid,
            expected_hash=self.content_hash,
            actual_hash=actual_hash,
        )

    def _block(self) -> str:
        return (
            f'<!-- adaptive-harness:start version="{self.version}" '
            f'hash="{self.content_hash}" -->\n'
            f"{self.body}\n"
            "<!-- adaptive-harness:end -->\n"
        )


def _managed_span(content: str) -> tuple[int, int] | None:
    starts = list(_START.finditer(content))
    ends = list(_END.finditer(content))
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1:
        raise ProjectionConflictError(
            "managed projection markers must appear exactly once"
        )
    start = starts[0]
    end = ends[0]
    if end.start() <= start.end():
        raise ProjectionConflictError("managed projection markers are misordered")
    span_end = end.end()
    if span_end < len(content) and content[span_end] == "\n":
        span_end += 1
    return start.start(), span_end


__all__ = [
    "ManagedProjection",
    "ProjectionConflictError",
    "ProjectionStatus",
]
