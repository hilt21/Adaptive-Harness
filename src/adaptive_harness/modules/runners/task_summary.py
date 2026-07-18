"""Builtin module process; intentionally imports no Harness runtime code."""

from __future__ import annotations

import json
import sys


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if request.get("protocol_version") != "1.0":
            raise ValueError("unsupported protocol")
        task = request.get("task")
        if not isinstance(task, dict):
            raise ValueError("task must be an object")
        response = {
            "status": "success",
            "summary": f"Task fields summarized: {len(task)}",
            "next_actions": [],
            "artifacts": [],
        }
        json.dump(response, sys.stdout, sort_keys=True)
        return 0
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
        json.dump(
            {
                "status": "failed",
                "summary": str(error),
                "next_actions": [],
                "artifacts": [],
            },
            sys.stdout,
            sort_keys=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
