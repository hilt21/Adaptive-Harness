"""Agent client integration adapters and managed projections."""

from adaptive_harness.adapters.base import (
    AdapterHealth,
    ApprovalRequest,
    ApprovalResponse,
    ApprovalUnavailableError,
    CapabilityProbe,
    ClientAdapter,
    ClientDetection,
    HealthState,
    IntegrationMode,
    ModelDetection,
    ToolCall,
    ToolDecision,
    ToolObservation,
)
from adaptive_harness.adapters.clients import (
    ADAPTER_TYPES,
    ClaudeCodeAdapter,
    CodexAdapter,
    GenericAdapter,
    adapter_for,
)
from adaptive_harness.adapters.integration import (
    IntegrationManager,
    IntegrationPlan,
)

__all__ = [
    "ADAPTER_TYPES",
    "AdapterHealth",
    "ApprovalRequest",
    "ApprovalResponse",
    "ApprovalUnavailableError",
    "CapabilityProbe",
    "ClaudeCodeAdapter",
    "ClientAdapter",
    "ClientDetection",
    "CodexAdapter",
    "GenericAdapter",
    "HealthState",
    "IntegrationMode",
    "IntegrationManager",
    "IntegrationPlan",
    "ModelDetection",
    "ToolCall",
    "ToolDecision",
    "ToolObservation",
    "adapter_for",
]
