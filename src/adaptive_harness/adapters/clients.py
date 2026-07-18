"""Generic, Codex, and Claude Code client adapters."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from adaptive_harness.adapters.base import (
    AdapterHealth,
    ApprovalCallback,
    CapabilityProbe,
    ClientAdapter,
    HealthState,
    IntegrationMode,
    ObservationCallback,
)
from adaptive_harness.adapters.claude_hook import settings_installed
from adaptive_harness.adapters.managed import (
    ManagedProjection,
    ProjectionConflictError,
)


class PromptProjectionAdapter(ClientAdapter):
    """Observe-only adapter backed by a managed instruction block."""

    projection_path: str

    def install_integration(self, existing: str | None) -> str:
        return ManagedProjection.agent_instructions().render(existing)

    def uninstall_integration(self, existing: str | None) -> str | None:
        if existing is None:
            return None
        return ManagedProjection.agent_instructions().remove(existing)

    def health_check(self) -> AdapterHealth:
        path = self.root / self.projection_path
        if not path.is_file():
            return AdapterHealth(
                HealthState.DEGRADED,
                IntegrationMode.OBSERVE,
                False,
                f"managed projection is absent: {self.projection_path}",
            )
        try:
            status = ManagedProjection.agent_instructions().inspect(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ProjectionConflictError) as error:
            return AdapterHealth(
                HealthState.FAILED,
                IntegrationMode.OBSERVE,
                True,
                f"managed projection cannot be verified: {error}",
            )
        if not status.valid:
            return AdapterHealth(
                HealthState.DEGRADED,
                IntegrationMode.OBSERVE,
                status.present,
                f"managed projection has drifted: {self.projection_path}",
            )
        return AdapterHealth(
            HealthState.HEALTHY,
            IntegrationMode.OBSERVE,
            True,
            "prompt-only integration is healthy but cannot enforce Gateway use",
        )


class GenericAdapter(ClientAdapter):
    client_id = "generic"


class CodexAdapter(PromptProjectionAdapter):
    client_id = "codex"
    projection_path = "AGENTS.md"
    detection_environment = ("CODEX_THREAD_ID", "CODEX_HOME")
    detection_directories = (".codex",)
    model_environment = ("CODEX_MODEL",)


class ClaudeCodeAdapter(PromptProjectionAdapter):
    client_id = "claude-code"
    projection_path = "CLAUDE.md"
    detection_environment = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")
    detection_directories = (".claude",)
    model_environment = ("ANTHROPIC_MODEL", "CLAUDE_MODEL")

    def capability_probe(self) -> CapabilityProbe:
        return CapabilityProbe(
            IntegrationMode.ENFORCED,
            can_intercept=True,
            verified_e2e=True,
            evidence=(
                "Claude Code PreToolUse hook blocks mutating tool bypass",
                "controlled capability launcher routes through Gateway and Executor",
            ),
        )

    def health_check(self) -> AdapterHealth:
        projection_health = super().health_check()
        settings_path = self.root / ".claude/settings.json"
        try:
            settings = (
                settings_path.read_text(encoding="utf-8")
                if settings_path.is_file()
                else None
            )
        except (OSError, UnicodeError) as error:
            return AdapterHealth(
                HealthState.FAILED,
                IntegrationMode.ENFORCED,
                True,
                f"Claude hook settings cannot be read: {error}",
            )
        if projection_health.state is not HealthState.HEALTHY or not settings_installed(
            settings
        ):
            return AdapterHealth(
                HealthState.DEGRADED,
                IntegrationMode.ENFORCED,
                settings is not None,
                "Claude enforced integration is incomplete or has drifted",
            )
        return AdapterHealth(
            HealthState.HEALTHY,
            IntegrationMode.ENFORCED,
            True,
            "Claude PreToolUse enforcement and prompt projection are current",
        )


ADAPTER_TYPES: dict[str, type[ClientAdapter]] = {
    "generic": GenericAdapter,
    "codex": CodexAdapter,
    "claude-code": ClaudeCodeAdapter,
}


def adapter_for(
    client_id: str,
    root: Path,
    *,
    environment: Mapping[str, str] | None = None,
    approval_callback: ApprovalCallback | None = None,
    observation_callback: ObservationCallback | None = None,
) -> ClientAdapter:
    try:
        adapter_type = ADAPTER_TYPES[client_id]
    except KeyError as error:
        raise ValueError(f"unsupported adapter: {client_id}") from error
    return adapter_type(
        root,
        environment=environment,
        approval_callback=approval_callback,
        observation_callback=observation_callback,
    )


__all__ = [
    "ADAPTER_TYPES",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "GenericAdapter",
    "PromptProjectionAdapter",
    "adapter_for",
]
