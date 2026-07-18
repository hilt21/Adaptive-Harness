"""Deterministic project scanners."""

from __future__ import annotations

from pathlib import Path

from adaptive_harness.scanners.generic import scan_generic
from adaptive_harness.scanners.node import scan_node
from adaptive_harness.scanners.profile import (
    ProfileFact,
    ProjectProfile,
    Provenance,
    build_profile,
)
from adaptive_harness.scanners.python import scan_python


def scan_project(path: Path) -> ProjectProfile:
    """Scan supported project facts without model inference or writes."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    facts = [
        *scan_generic(root),
        *scan_python(root),
        *scan_node(root),
    ]
    if not any(fact.category == "test_command" for fact in facts):
        facts.append(
            ProfileFact(
                category="test_command",
                value=None,
                provenance=Provenance.UNKNOWN,
                evidence="no supported test command detected",
            )
        )
    return build_profile(root, facts)


__all__ = ["scan_project"]
