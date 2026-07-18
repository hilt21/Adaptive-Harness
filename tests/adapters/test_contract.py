from pathlib import Path

import pytest

from adaptive_harness.adapters import (
    ApprovalRequest,
    ApprovalResponse,
    ApprovalUnavailableError,
    CapabilityProbe,
    ClaudeCodeAdapter,
    ClientAdapter,
    CodexAdapter,
    GenericAdapter,
    HealthState,
    IntegrationMode,
    ToolCall,
    ToolObservation,
)
from adaptive_harness.adapters.claude_hook import install_settings

ADAPTERS = (GenericAdapter, CodexAdapter, ClaudeCodeAdapter)


@pytest.mark.parametrize("adapter_type", ADAPTERS)
def test_every_adapter_implements_contract(
    tmp_path: Path, adapter_type: type[ClientAdapter]
) -> None:
    adapter = adapter_type(tmp_path)

    for method in (
        "detect_client",
        "detect_model",
        "capability_probe",
        "install_integration",
        "uninstall_integration",
        "before_tool_call",
        "after_tool_call",
        "request_user_approval",
        "deliver_observation",
        "health_check",
    ):
        assert callable(getattr(adapter, method))


@pytest.mark.parametrize(
    ("adapter_type", "environment", "model"),
    [
        (CodexAdapter, {"CODEX_THREAD_ID": "thread", "CODEX_MODEL": "gpt"}, "gpt"),
        (
            ClaudeCodeAdapter,
            {"CLAUDE_CODE_SESSION_ID": "session", "ANTHROPIC_MODEL": "sonnet"},
            "sonnet",
        ),
    ],
)
def test_client_and_model_detection_use_observed_evidence(
    tmp_path: Path,
    adapter_type: type[ClientAdapter],
    environment: dict[str, str],
    model: str,
) -> None:
    adapter = adapter_type(tmp_path, environment=environment)

    assert adapter.detect_client().detected is True
    assert adapter.detect_client().evidence
    assert adapter.detect_model().model_id == model
    assert adapter.detect_model().evidence is not None


@pytest.mark.parametrize("adapter_type", (GenericAdapter, CodexAdapter))
def test_prompt_capabilities_never_claim_enforcement(
    tmp_path: Path, adapter_type: type[ClientAdapter]
) -> None:
    probe = adapter_type(tmp_path).capability_probe()

    assert probe.mode is IntegrationMode.OBSERVE
    assert probe.can_intercept is False
    assert probe.verified_e2e is False


def test_claude_native_hook_claims_only_verified_enforcement(tmp_path: Path) -> None:
    probe = ClaudeCodeAdapter(tmp_path).capability_probe()

    assert probe.mode is IntegrationMode.ENFORCED
    assert probe.can_intercept is True
    assert probe.verified_e2e is True


def test_invalid_enforced_probe_is_rejected() -> None:
    with pytest.raises(ValueError, match="enforced"):
        CapabilityProbe(IntegrationMode.ENFORCED, False, False, ())


@pytest.mark.parametrize(
    ("adapter_type", "projection_path"),
    [(CodexAdapter, "AGENTS.md"), (ClaudeCodeAdapter, "CLAUDE.md")],
)
def test_projection_lifecycle_preserves_user_content(
    tmp_path: Path,
    adapter_type: type[ClientAdapter],
    projection_path: str,
) -> None:
    adapter = adapter_type(tmp_path)
    original = "# User instructions\nKeep this.\n"

    installed = adapter.install_integration(original)

    assert installed is not None
    assert installed.startswith(original)
    assert "adaptive-harness:start" in installed
    (tmp_path / projection_path).write_text(installed, encoding="utf-8")
    if adapter_type is ClaudeCodeAdapter:
        settings = tmp_path / ".claude/settings.json"
        settings.parent.mkdir()
        settings.write_text(install_settings(None), encoding="utf-8")
    health = adapter.health_check()
    assert health.state is HealthState.HEALTHY
    expected_mode = (
        IntegrationMode.ENFORCED
        if adapter_type is ClaudeCodeAdapter
        else IntegrationMode.OBSERVE
    )
    assert health.mode is expected_mode
    assert adapter.uninstall_integration(installed) == original


def test_observe_tool_hooks_and_host_callbacks(tmp_path: Path) -> None:
    observations: list[ToolObservation] = []
    adapter = CodexAdapter(
        tmp_path,
        approval_callback=lambda request: ApprovalResponse(
            request.capability_id == "tests", "approved in host"
        ),
        observation_callback=observations.append,
    )
    call = ToolCall("call-1", "shell", {"argv": ["pytest"]}, True)

    decision = adapter.before_tool_call(call)
    observation = adapter.after_tool_call(call, succeeded=True, summary="passed")
    approval = adapter.request_user_approval(ApprovalRequest("tests", "run tests"))
    adapter.deliver_observation(observation)

    assert decision.allowed is True
    assert decision.routed_via_gateway is False
    assert decision.mode is IntegrationMode.OBSERVE
    assert observations == [observation]
    assert approval.approved is True


def test_missing_approval_channel_fails_explicitly(tmp_path: Path) -> None:
    with pytest.raises(ApprovalUnavailableError):
        GenericAdapter(tmp_path).request_user_approval(
            ApprovalRequest("tests", "run tests")
        )
