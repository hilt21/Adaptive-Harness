import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from adaptive_harness import __version__
from adaptive_harness.cli import main
from adaptive_harness.core.workspace import GitWorkspace
from adaptive_harness.feedback import FeedbackEpisode, FeedbackMode, FeedbackStore
from adaptive_harness.storage import StorageItem, StorageManager

FIXTURES = Path(__file__).parents[2] / "fixtures"


def copy_git_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(FIXTURES / "python-project", root)
    subprocess.run(("git", "-C", str(root), "init", "-q"), check=True)
    subprocess.run(
        ("git", "-C", str(root), "config", "user.name", "Harness Tests"),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "config",
            "user.email",
            "harness@example.invalid",
        ),
        check=True,
    )
    subprocess.run(("git", "-C", str(root), "add", "."), check=True)
    subprocess.run(
        ("git", "-C", str(root), "commit", "-q", "-m", "initial"),
        check=True,
    )
    return root


def test_empty_cli_invocation_succeeds() -> None:
    assert main([]) == 0


def test_cli_reports_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--version"])

    assert error.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_zh_cn_human_output_does_not_change_json_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as help_exit:
        main(["--locale", "zh-CN", "--help"])
    assert help_exit.value.code == 0
    help_output = capsys.readouterr().out
    assert "确定性的项目初始化" in help_output
    assert "adapter-hook" not in help_output

    root = copy_git_fixture(tmp_path)
    assert main(
        [
            "--locale",
            "zh-CN",
            "init",
            "--root",
            str(root),
            "--apply",
            "--yes",
        ]
    ) == 0
    assert "Doctor 检查通过" in capsys.readouterr().out
    assert main(
        ["--locale", "zh-CN", "doctor", "--root", str(root), "--json"]
    ) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["ok"] is True
    assert document["checks"][0]["status"] == "pass"


def test_init_without_apply_only_prints_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)

    exit_code = main(
        ["init", "--root", str(root), "--adapter", "generic"]
    )

    assert exit_code == 0
    assert not (root / ".harness/config.json").exists()
    assert ".harness/config.json" in capsys.readouterr().out


def test_init_apply_requires_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)

    exit_code = main(
        ["init", "--root", str(root), "--adapter", "generic", "--apply"],
        input_fn=lambda _: "no",
    )

    assert exit_code == 1
    assert not (root / ".harness/config.json").exists()
    assert "cancelled" in capsys.readouterr().out.lower()


def test_init_apply_and_doctor_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)

    init_exit = main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "generic",
            "--apply",
            "--yes",
            "--json",
        ]
    )
    init_output = json.loads(capsys.readouterr().out)
    doctor_exit = main(["doctor", "--root", str(root), "--json"])
    doctor_output = json.loads(capsys.readouterr().out)

    assert init_exit == 0
    assert init_output["status"] == "applied"
    assert doctor_exit == 0
    assert doctor_output["ok"] is True


def test_init_json_plan_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)

    exit_code = main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "codex",
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "planned"
    assert "AGENTS.md" in output["files"]


def test_integration_list_reports_observe_only_capabilities(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)

    exit_code = main(
        ["integration", "list", "--root", str(root), "--json"]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert {entry["id"] for entry in output["adapters"]} == {
        "generic",
        "codex",
        "claude-code",
    }
    modes = {entry["id"]: entry["mode"] for entry in output["adapters"]}
    assert modes == {
        "generic": "observe",
        "codex": "observe",
        "claude-code": "enforced",
    }
    verified = {entry["id"]: entry["verified_e2e"] for entry in output["adapters"]}
    assert verified == {"generic": False, "codex": False, "claude-code": True}


def test_integration_cli_plans_and_applies_lifecycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)
    assert main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "generic",
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    plan_exit = main(
        ["integration", "install", "codex", "--root", str(root), "--json"]
    )
    plan = json.loads(capsys.readouterr().out)
    apply_exit = main(
        [
            "integration",
            "install",
            "codex",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    )
    applied = json.loads(capsys.readouterr().out)

    assert plan_exit == apply_exit == 0
    assert plan["status"] == "planned"
    assert applied["status"] == "applied"
    assert "adaptive-harness:start" in (root / "AGENTS.md").read_text(
        encoding="utf-8"
    )


def test_module_and_template_cli_are_explicit_and_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)
    assert main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "generic",
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    assert main(["module", "list", "--root", str(root), "--json"]) == 0
    modules = json.loads(capsys.readouterr().out)
    assert all(item["state"] == "installed" for item in modules["modules"])
    assert main(
        [
            "module",
            "enable",
            "tdd-guidance",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"

    output = root / "docs/handoff.md"
    assert main(
        [
            "template",
            "render",
            "handoff",
            "--output",
            "docs/handoff.md",
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    assert not output.exists()
    capsys.readouterr()
    assert main(
        [
            "template",
            "render",
            "handoff",
            "--output",
            "docs/handoff.md",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    assert output.read_text(encoding="utf-8").startswith("# Handoff")


def test_feedback_mode_show_and_suggest_are_local_and_reviewable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)
    data_root = tmp_path / "local-data"
    assert main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "generic",
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "feedback",
            "mode",
            "research",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"

    repository_id = GitWorkspace(root).snapshot().repository_id
    store = FeedbackStore(data_root, repository_id, mode=FeedbackMode.RESEARCH)
    for index in range(3):
        store.record(
            FeedbackEpisode(
                episode_id=f"episode-{index}",
                task_id=f"task-{index}",
                created_at="2026-07-16T00:00:00Z",
                module_ids=("tdd-guidance",),
                template_ids=(),
                capability_ids=(),
                approval_event_ids=(),
                command_event_ids=(),
                failure=None,
                coverage_percent=90.0,
                diff_paths=("src/app.py",),
                result="completed",
                duration_ms=100,
            )
        )

    assert main(
        [
            "feedback",
            "show",
            "--root",
            str(root),
            "--data-root",
            str(data_root),
            "--json",
        ]
    ) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["episode_count"] == 3
    assert shown["telemetry"] == "disabled"
    assert main(
        [
            "suggest",
            "--root",
            str(root),
            "--data-root",
            str(data_root),
            "--json",
        ]
    ) == 0
    suggestion = json.loads(capsys.readouterr().out)["suggestions"][0]
    assert suggestion["maturity"] == "recommendation"
    assert ".harness/modules.lock.json" in suggestion["config_diff"]


def test_storage_export_and_upgrade_cli_lifecycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)
    data_root = tmp_path / "local-data"
    assert main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "generic",
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    repository_id = GitWorkspace(root).snapshot().repository_id
    storage = StorageManager(data_root, repository_id)
    artifact = storage.root / "artifacts/old.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("old", encoding="utf-8")
    storage.register(
        StorageItem(
            "artifact-1",
            "artifact",
            "artifacts/old.txt",
            (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            "completed",
        )
    )

    assert main(
        [
            "storage",
            "status",
            "--root",
            str(root),
            "--data-root",
            str(data_root),
            "--json",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["item_count"] == 1
    assert main(
        [
            "storage",
            "prune",
            "--root",
            str(root),
            "--data-root",
            str(data_root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert not artifact.exists()

    export_path = tmp_path / "export.json"
    assert main(
        [
            "export",
            "--root",
            str(root),
            "--data-root",
            str(data_root),
            "--output",
            str(export_path),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert export_path.is_file()

    config_path = root / ".harness/config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["runtime_version"] = "0.0.0"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    assert main(
        [
            "upgrade",
            "apply",
            "--root",
            str(root),
            "--data-root",
            str(data_root),
            "--yes",
            "--json",
        ]
    ) == 0
    upgraded = json.loads(capsys.readouterr().out)
    assert upgraded["status"] == "applied"
    assert main(
        [
            "upgrade",
            "rollback",
            "--root",
            str(root),
            "--data-root",
            str(data_root),
            "--recovery-id",
            upgraded["recovery_id"],
            "--yes",
            "--json",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert json.loads(config_path.read_text(encoding="utf-8"))[
        "runtime_version"
    ] == "0.0.0"


def test_task_cli_start_amend_verify_accept_risk_and_cancel(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)
    data_root = tmp_path / "task-data"
    assert main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "generic",
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    subprocess.run(("git", "-C", str(root), "add", ".harness"), check=True)
    subprocess.run(
        ("git", "-C", str(root), "commit", "-q", "-m", "initialize harness"),
        check=True,
    )

    common = ["--root", str(root), "--data-root", str(data_root), "--json"]
    assert main(
        [
            "task",
            "start",
            "--id",
            "task-cli-1",
            "--goal",
            "Fix the example",
            "--scope",
            "src",
            *common,
        ]
    ) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["state"] == "draft"
    assert started["envelope_revisions"][0]["base_sha"] == subprocess.run(
        ("git", "-C", str(root), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert main(
        [
            "task",
            "amend",
            "task-cli-1",
            "--add-scope",
            "tests",
            *common,
        ]
    ) == 0
    amended = json.loads(capsys.readouterr().out)
    assert amended["envelope_revisions"][-1]["revision"] == 2
    assert main(["task", "verify", "task-cli-1", *common]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["outcome"] == "rejected"
    assert main(
        [
            "task",
            "accept-risk",
            "task-cli-1",
            "--reason",
            "Acceptance evidence unavailable in this fixture",
            *common,
        ]
    ) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["outcome"] == "accepted_with_risk"

    assert main(
        [
            "task",
            "start",
            "--id",
            "task-cli-2",
            "--goal",
            "Cancel me",
            "--scope",
            "src",
            *common,
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "cancel",
            "task-cli-2",
            "--reason",
            "requirements changed",
            *common,
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "abandoned"


def test_capability_cli_requires_and_consumes_scoped_approval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)
    data_root = tmp_path / "approval-data"
    assert main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "generic",
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    capabilities_path = root / ".harness/capabilities.json"
    capabilities = json.loads(capabilities_path.read_text(encoding="utf-8"))
    capability = capabilities["capabilities"][0]
    capability["argv"] = [sys.executable, "-c", "raise SystemExit(0)"]
    capability["approval_policy"] = "ask"
    capabilities_path.write_text(
        json.dumps(capabilities, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    subprocess.run(("git", "-C", str(root), "add", ".harness"), check=True)
    subprocess.run(
        ("git", "-C", str(root), "commit", "-q", "-m", "approval fixture"),
        check=True,
    )
    common = ["--root", str(root), "--data-root", str(data_root), "--json"]
    assert main(
        [
            "task",
            "start",
            "--id",
            "approval-task",
            "--goal",
            "Run an approved operation",
            "--scope",
            "src",
            "--capability",
            "project-tests",
            *common,
        ]
    ) == 0
    capsys.readouterr()

    run = [
        "capability",
        "run",
        "--task",
        "approval-task",
        "--capability",
        "project-tests",
        *common,
    ]
    assert main(run) == 1
    assert "scoped approval" in json.loads(capsys.readouterr().out)["message"]
    approve = [
        "capability",
        "approve",
        "--task",
        "approval-task",
        "--capability",
        "project-tests",
        *common,
    ]
    assert main(approve) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "planned"
    assert plan["base_sha"]
    assert plan["max_uses"] == 1
    assert main([*approve, "--apply", "--yes"]) == 0
    granted = json.loads(capsys.readouterr().out)
    assert granted["status"] == "applied"
    assert main(run) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"
    assert main(run) == 1
    assert "scoped approval" in json.loads(capsys.readouterr().out)["message"]


def test_three_task_progressive_module_loading_and_trial_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)
    data_root = tmp_path / "progressive-data"
    assert main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "generic",
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    common = ["--root", str(root), "--data-root", str(data_root), "--json"]

    def start(task_id: str, *extra: str) -> dict[str, Any]:
        assert main(
            [
                "task",
                "start",
                "--id",
                task_id,
                "--goal",
                "Change example code",
                "--scope",
                "src",
                "--trait",
                "code-change",
                *extra,
                *common,
            ]
        ) == 0
        return cast(dict[str, Any], json.loads(capsys.readouterr().out))

    installed = start("progressive-1")
    installed_event = next(
        event
        for event in installed["events"]
        if event["type"] == "module.activation"
        and event["data"]["module_id"] == "tdd-guidance"
    )
    assert installed_event["data"]["state"] == "installed"
    assert installed_event["data"]["context"] is None

    assert main(
        [
            "module",
            "enable",
            "tdd-guidance",
            "--policy",
            "auto",
            "--apply",
            "--yes",
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    automatic = start("progressive-2")
    automatic_event = next(
        event
        for event in automatic["events"]
        if event["type"] == "module.activation"
        and event["data"]["module_id"] == "tdd-guidance"
    )
    assert automatic_event["data"]["state"] == "activated"
    assert automatic_event["data"]["context"]

    assert main(
        [
            "module",
            "enable",
            "tdd-guidance",
            "--policy",
            "manual",
            "--apply",
            "--yes",
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    manual = start("progressive-3", "--module", "tdd-guidance")
    manual_event = next(
        event
        for event in manual["events"]
        if event["type"] == "module.activation"
        and event["data"]["module_id"] == "tdd-guidance"
    )
    assert manual_event["data"]["state"] == "activated"
    assert manual_event["data"]["context"]

    mutation_common = ["--root", str(root), "--apply", "--yes", "--json"]
    assert main(
        ["module", "trial", "tdd-guidance", "--tasks", "1", *mutation_common]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "module",
            "trial-result",
            "tdd-guidance",
            "beneficial",
            "--task",
            "progressive-3",
            "--evidence-ref",
            "record:progressive-3",
            *mutation_common,
        ]
    ) == 0
    capsys.readouterr()
    assert main(["module", "promote", "tdd-guidance", *mutation_common]) == 0
    capsys.readouterr()
    lock = json.loads(
        (root / ".harness/modules.lock.json").read_text(encoding="utf-8")
    )
    assert lock["trials"][-1]["state"] == "promoted"


def test_config_cli_explain_diff_and_apply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)
    assert main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "codex",
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    agents = root / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace(
            "Use Adaptive Harness", "Drifted Harness"
        ),
        encoding="utf-8",
    )

    assert main(["config", "explain", "--root", str(root), "--json"]) == 0
    explanation = json.loads(capsys.readouterr().out)
    assert explanation["provenance"] == "canonical-local-json"
    assert main(["config", "diff", "--root", str(root), "--json"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert "AGENTS.md" in planned["files"]
    assert main(
        ["config", "apply", "--root", str(root), "--yes", "--json"]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert "Use Adaptive Harness" in agents.read_text(encoding="utf-8")


@pytest.mark.skipif(
    os.environ.get("HARNESS_RUN_OS_SANDBOX_E2E") != "1",
    reason="requires host sandbox-exec outside the test runner sandbox",
)
def test_claude_enforced_capability_path_completes_with_sandboxed_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = copy_git_fixture(tmp_path)
    data_root = tmp_path / "enforced-data"
    assert main(
        [
            "init",
            "--root",
            str(root),
            "--adapter",
            "claude-code",
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    capabilities_path = root / ".harness/capabilities.json"
    capabilities = json.loads(capabilities_path.read_text(encoding="utf-8"))
    capabilities["capabilities"][0]["argv"] = [
        sys.executable,
        "-c",
        "raise SystemExit(0)",
    ]
    capabilities_path.write_text(
        json.dumps(capabilities, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        ("git", "-C", str(root), "add", ".harness", ".claude", "CLAUDE.md"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(root), "commit", "-q", "-m", "enforced harness"),
        check=True,
    )
    common = ["--root", str(root), "--data-root", str(data_root), "--json"]
    assert main(
        [
            "task",
            "start",
            "--id",
            "enforced-task",
            "--goal",
            "Run enforced acceptance",
            "--scope",
            "src",
            "--capability",
            "project-tests",
            *common,
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "capability",
            "run",
            "--task",
            "enforced-task",
            "--capability",
            "project-tests",
            *common,
        ]
    ) == 0
    execution = json.loads(capsys.readouterr().out)
    assert execution["status"] == "succeeded"
    assert main(["task", "verify", "enforced-task", *common]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "completed"
