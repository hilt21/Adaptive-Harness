"""Standalone Runtime installation lifecycle."""

from adaptive_harness.distribution.manager import (
    SelfManager,
    UninstallPlan,
    UninstallResult,
    UpdateResult,
)

__all__ = ["SelfManager", "UninstallPlan", "UninstallResult", "UpdateResult"]
