"""Claude Code native hook protocol enforcement."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Any

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "WebSearch", "WebFetch"}
HOOK_COMMAND = "harness adapter-hook claude-code || exit 2"
HOOK_MATCHER = "Bash|Edit|Write|NotebookEdit|MultiEdit|mcp__.*"


@dataclass(frozen=True, slots=True)
class ClaudeHookDecision:
    allowed: bool
    reason: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow" if self.allowed else "deny",
                    "permissionDecisionReason": self.reason,
                }
            },
            sort_keys=True,
        )


def decide_pre_tool_use(value: object) -> ClaudeHookDecision:
    if not isinstance(value, dict) or value.get("hook_event_name") != "PreToolUse":
        return ClaudeHookDecision(False, "invalid or unsupported hook event")
    tool_name = value.get("tool_name")
    tool_input = value.get("tool_input")
    if tool_name in _READ_ONLY_TOOLS:
        return ClaudeHookDecision(True, "read-only tool allowed")
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return ClaudeHookDecision(
            False,
            "mutating tools must execute through `harness capability run`",
        )
    command = tool_input.get("command")
    if not isinstance(command, str):
        return ClaudeHookDecision(False, "Bash command is missing")
    try:
        arguments = shlex.split(command, posix=True)
    except ValueError:
        return ClaudeHookDecision(False, "Bash command cannot be parsed safely")
    if len(arguments) != 7 or arguments[:3] != [
        "harness",
        "capability",
        "run",
    ]:
        return ClaudeHookDecision(
            False,
            "only the exact controlled capability launcher is allowed",
        )
    options = {arguments[3]: arguments[4], arguments[5]: arguments[6]}
    if set(options) != {"--task", "--capability"} or not all(
        _IDENTIFIER.fullmatch(item) for item in options.values()
    ):
        return ClaudeHookDecision(False, "capability launcher arguments are invalid")
    return ClaudeHookDecision(True, "Gateway and Executor will govern this operation")


def managed_hook() -> dict[str, Any]:
    return {
        "matcher": HOOK_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": HOOK_COMMAND,
                "timeout": 5,
                "statusMessage": "Adaptive Harness policy check",
            }
        ],
    }


def install_settings(existing: str | None) -> str:
    if existing is None or not existing.strip():
        document: dict[str, Any] = {}
    else:
        value = json.loads(existing)
        if not isinstance(value, dict):
            raise ValueError("Claude settings root must be an object")
        document = value
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Claude settings hooks must be an object")
    pre_tool = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre_tool, list):
        raise ValueError("Claude PreToolUse hooks must be an array")
    pre_tool[:] = [item for item in pre_tool if not _is_managed_hook(item)]
    pre_tool.append(managed_hook())
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def uninstall_settings(existing: str | None) -> str | None:
    if existing is None:
        return None
    value = json.loads(existing)
    if not isinstance(value, dict):
        raise ValueError("Claude settings root must be an object")
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        return existing
    pre_tool = hooks.get("PreToolUse")
    if not isinstance(pre_tool, list):
        return existing
    pre_tool[:] = [item for item in pre_tool if not _is_managed_hook(item)]
    if not pre_tool:
        hooks.pop("PreToolUse", None)
    if not hooks:
        value.pop("hooks", None)
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def settings_installed(existing: str | None) -> bool:
    if existing is None:
        return False
    try:
        value = json.loads(existing)
    except json.JSONDecodeError:
        return False
    if not isinstance(value, dict):
        return False
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        return False
    pre_tool = hooks.get("PreToolUse")
    return isinstance(pre_tool, list) and sum(
        _is_managed_hook(item) for item in pre_tool
    ) == 1


def _is_managed_hook(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    hooks = value.get("hooks")
    return isinstance(hooks, list) and any(
        isinstance(item, dict) and item.get("command") == HOOK_COMMAND
        for item in hooks
    )


__all__ = [
    "ClaudeHookDecision",
    "HOOK_COMMAND",
    "HOOK_MATCHER",
    "decide_pre_tool_use",
    "install_settings",
    "managed_hook",
    "settings_installed",
    "uninstall_settings",
]
