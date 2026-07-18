"""Progressively activated optional modules."""

from adaptive_harness.modules.manager import ModuleManager, ModulePlan, ModuleStatus
from adaptive_harness.modules.model import (
    ActivationDecision,
    ActivationPolicy,
    ActivationState,
    ModuleCost,
    ModuleManifest,
    ModuleType,
    decide_activation,
)
from adaptive_harness.modules.registry import ModuleRegistry
from adaptive_harness.modules.runner import (
    ModuleExecutionError,
    ModuleRunner,
    ModuleRunResult,
)

__all__ = [
    "ActivationDecision",
    "ActivationPolicy",
    "ActivationState",
    "ModuleCost",
    "ModuleExecutionError",
    "ModuleManifest",
    "ModuleManager",
    "ModulePlan",
    "ModuleRegistry",
    "ModuleRunResult",
    "ModuleRunner",
    "ModuleStatus",
    "ModuleType",
    "decide_activation",
]
