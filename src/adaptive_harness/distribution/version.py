"""Semantic Versioning 2.0.0 validation for release contracts."""

from __future__ import annotations

import re

_CORE = r"(?:0|[1-9][0-9]*)"
_PRERELEASE_IDENTIFIER = (
    r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
)
SEMVER_PATTERN = (
    rf"{_CORE}\.{_CORE}\.{_CORE}"
    rf"(?:-{_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_SEMVER = re.compile(SEMVER_PATTERN)


def is_semver(value: str) -> bool:
    """Return whether *value* is a complete SemVer 2.0.0 string."""
    return _SEMVER.fullmatch(value) is not None


__all__ = ["SEMVER_PATTERN", "is_semver"]
