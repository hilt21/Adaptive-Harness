import json
from pathlib import Path

from adaptive_harness.adapters.claude_hook import (
    HOOK_COMMAND,
    decide_pre_tool_use,
    install_settings,
    settings_installed,
    uninstall_settings,
)


def _event(tool_name: str, tool_input: dict[str, object]) -> dict[str, object]:
    return {
        "session_id": "session-1",
        "cwd": "/project",
        "permission_mode": "bypassPermissions",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def test_pre_tool_hook_blocks_bypass_and_allows_controlled_launcher() -> None:
    edit = decide_pre_tool_use(_event("Edit", {"file_path": "src/app.py"}))
    arbitrary_bash = decide_pre_tool_use(
        _event("Bash", {"command": "python -c 'open(\"bad\", \"w\")'"})
    )
    controlled = decide_pre_tool_use(
        _event(
            "Bash",
            {
                "command": (
                    "harness capability run --task task-1 "
                    "--capability project-tests"
                )
            },
        )
    )

    assert edit.allowed is False
    assert arbitrary_bash.allowed is False
    assert controlled.allowed is True
    assert json.loads(edit.to_json())["hookSpecificOutput"][
        "permissionDecision"
    ] == "deny"


def test_settings_lifecycle_preserves_user_hooks_and_is_idempotent(
    tmp_path: Path,
) -> None:
    existing = json.dumps(
        {
            "permissions": {"deny": ["Read(.env)"]},
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [{"type": "command", "command": "user-hook"}],
                    }
                ]
            },
        }
    )

    installed = install_settings(existing)
    repeated = install_settings(installed)

    assert installed == repeated
    assert settings_installed(installed) is True
    document = json.loads(installed)
    commands = [
        hook["command"]
        for group in document["hooks"]["PreToolUse"]
        for hook in group["hooks"]
    ]
    assert commands == ["user-hook", HOOK_COMMAND]
    removed = uninstall_settings(installed)
    assert removed is not None
    restored = json.loads(removed)
    assert restored["permissions"] == {"deny": ["Read(.env)"]}
    assert restored["hooks"]["PreToolUse"][0]["hooks"][0][
        "command"
    ] == "user-hook"
