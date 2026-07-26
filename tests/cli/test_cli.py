import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast

import pytest

from adaptive_harness import __version__
from adaptive_harness.cli import main
from adaptive_harness.core.task_service import TaskService
from adaptive_harness.core.workspace import GitWorkspace
from adaptive_harness.feedback import FeedbackEpisode, FeedbackMode, FeedbackStore
from adaptive_harness.storage import (
    StorageItem,
    StorageLocator,
    StorageManager,
    StorageMode,
)

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


def test_runtime_module_is_a_standalone_builder_entrypoint() -> None:
    completed = subprocess.run(
        (sys.executable, "-m", "adaptive_harness", "--version"),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == __version__


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
    locator = StorageLocator(root, data_root)
    store = FeedbackStore(
        data_root,
        repository_id,
        mode=FeedbackMode.RESEARCH,
        storage_locator=locator,
        force_user_data=True,
    )
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
    locator = StorageLocator(root, data_root)
    storage = StorageManager(
        data_root,
        repository_id,
        storage_locator=locator,
        force_user_data=True,
    )
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


def test_storage_mode_reports_default_user_data_location(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    xdg_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))

    assert main(["storage", "mode", "--root", str(root), "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    repository_id = GitWorkspace(root).snapshot().repository_id
    assert document == {
        "mode": "user-data",
        "project_data": str(xdg_data / "harness/projects" / repository_id),
        "scope": "local-clone",
    }


def test_default_storage_isolated_between_repositories(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_parent = tmp_path / "first"
    second_parent = tmp_path / "second"
    first_parent.mkdir()
    second_parent.mkdir()
    first = copy_git_fixture(first_parent)
    second = copy_git_fixture(second_parent)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

    assert main(["storage", "mode", "--root", str(first), "--json"]) == 0
    first_location = json.loads(capsys.readouterr().out)["project_data"]
    assert main(["storage", "mode", "--root", str(second), "--json"]) == 0
    second_location = json.loads(capsys.readouterr().out)["project_data"]

    assert first_location != second_location


def test_storage_migration_copies_verifies_and_switches_local_clone_mode(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    xdg_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
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
    source = xdg_data / "harness/projects" / repository_id
    evidence = source / "artifacts/evidence.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("trusted evidence\n", encoding="utf-8")
    common_dir = Path(
        subprocess.check_output(
            (
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ),
            text=True,
        ).strip()
    )
    target = common_dir / "adaptive-harness"

    assert main(
        ["storage", "migrate", "repository-local", "--root", str(root), "--json"]
    ) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["status"] == "planned"
    assert planned["source"] == str(source)
    assert planned["target"] == str(target)
    assert planned["item_count"] == 1
    assert not target.exists()

    assert main(
        [
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["status"] == "applied"
    assert (target / "artifacts/evidence.txt").read_text(encoding="utf-8") == (
        "trusted evidence\n"
    )
    assert evidence.is_file()
    assert subprocess.check_output(
        ("git", "-C", str(root), "config", "--local", "--get", "harness.storageMode"),
        text=True,
    ).strip() == "repository-local"

    assert main(["storage", "mode", "--root", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["project_data"] == str(target)

    assert main(
        [
            "task",
            "start",
            "--id",
            "repository-local-task",
            "--goal",
            "Verify repository-local storage",
            "--scope",
            "src",
            "--capability",
            "project-tests",
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    task_data = StorageLocator(root, xdg_data / "harness").location().task_data
    assert (task_data / "tasks/repository-local-task/record.json").is_file()


def test_storage_migration_rejects_unfinished_task(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    assert main(
        ["init", "--root", str(root), "--apply", "--yes", "--json"]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "start",
            "--id",
            "unfinished-task",
            "--goal",
            "Remain unfinished",
            "--scope",
            "src",
            "--capability",
            "project-tests",
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        ["storage", "migrate", "repository-local", "--root", str(root), "--json"]
    ) == 1

    error = json.loads(capsys.readouterr().out)
    assert "unfinished-task is draft" in error["message"]
    assert subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "config",
            "--local",
            "--get",
            "harness.storageMode",
        ),
        capture_output=True,
        check=False,
    ).returncode == 1


def test_storage_migration_reports_existing_target_conflicts_before_apply(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    common_dir = Path(
        subprocess.check_output(
            (
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ),
            text=True,
        ).strip()
    )
    conflict = common_dir / "adaptive-harness/existing.json"
    conflict.parent.mkdir()
    conflict.write_text("{}\n", encoding="utf-8")

    assert main(
        ["storage", "migrate", "repository-local", "--root", str(root), "--json"]
    ) == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan["status"] == "planned"
    assert plan["item_count"] == 0
    assert plan["bytes_to_copy"] == 0
    assert plan["conflicts"] == ["existing.json"]
    assert plan["target"] == str(conflict.parent)
    assert conflict.is_file()

    assert main(
        [
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err)["conflicts"] == ["existing.json"]
    assert "target has conflicts" in json.loads(captured.out)["message"]
    assert conflict.is_file()


def test_storage_migration_keeps_source_mode_when_switch_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    xdg_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    repository_id = GitWorkspace(root).snapshot().repository_id
    source_record = xdg_data / f"harness/projects/{repository_id}/record.json"
    source_record.parent.mkdir(parents=True)
    source_record.write_text("source\n", encoding="utf-8")
    common_dir = Path(
        subprocess.check_output(
            (
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ),
            text=True,
        ).strip()
    )
    target = common_dir / "adaptive-harness"

    def fail_mode_switch(self: StorageLocator, mode: object) -> None:
        raise ValueError("simulated local config failure")

    monkeypatch.setattr(StorageLocator, "set_mode", fail_mode_switch)

    assert main(
        [
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 1

    assert "simulated local config failure" in json.loads(
        capsys.readouterr().out
    )["message"]
    assert source_record.read_text(encoding="utf-8") == "source\n"
    assert not target.exists()
    assert subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "config",
            "--local",
            "--get",
            "harness.storageMode",
        ),
        capture_output=True,
        check=False,
    ).returncode == 1


def test_storage_migration_can_rollback_to_unchanged_retained_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    xdg_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    repository_id = GitWorkspace(root).snapshot().repository_id
    retained = xdg_data / f"harness/projects/{repository_id}"
    evidence = retained / "artifacts/evidence.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("trusted evidence\n", encoding="utf-8")

    assert main(
        [
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    assert main(
        [
            "storage",
            "migrate",
            "user-data",
            "--rollback",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "rolled_back"
    assert result["target"] == str(retained)
    assert evidence.read_text(encoding="utf-8") == "trusted evidence\n"
    assert main(["storage", "mode", "--root", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "user-data"


def test_storage_migration_shows_plan_before_interactive_confirmation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    xdg_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    repository_id = GitWorkspace(root).snapshot().repository_id
    source = xdg_data / f"harness/projects/{repository_id}"
    source.mkdir(parents=True)
    (source / "record.json").write_text("record\n", encoding="utf-8")
    output_before_prompt: list[str] = []

    def decline(_: str) -> str:
        output_before_prompt.append(capsys.readouterr().out)
        return "n"

    assert main(
        [
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--apply",
        ],
        input_fn=decline,
    ) == 1

    assert "Storage migration planned." in output_before_prompt[0]
    assert f"Source: {source}" in output_before_prompt[0]
    assert "Items: 1; bytes: 7" in output_before_prompt[0]


def test_zh_cn_storage_migration_cancellation_is_fully_localized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

    assert main(
        [
            "--locale",
            "zh-CN",
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--apply",
        ],
        input_fn=lambda prompt: "n" if "存储迁移" in prompt else "unexpected",
    ) == 1

    output = capsys.readouterr().out
    assert "存储迁移已取消；数据未发生变化。" in output
    assert "storage migrate" not in output.lower()


def test_empty_storage_migration_can_rollback_without_a_retained_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

    assert main(
        [
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    forward = json.loads(capsys.readouterr().out)
    assert forward["source_retained"] is False

    assert main(
        [
            "storage",
            "migrate",
            "user-data",
            "--rollback",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "rolled_back"


def test_storage_mode_supports_localized_verbose_human_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = copy_git_fixture(tmp_path)

    assert main(
        [
            "--locale",
            "zh-CN",
            "storage",
            "mode",
            "--root",
            str(root),
            "--verbose",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert "存储模式：user-data" in output
    assert "项目数据：" in output
    assert "作用域：local-clone" in output


def test_storage_status_prune_and_pin_support_localized_human_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    xdg_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    locator = StorageLocator(root, xdg_data / "harness")
    location = locator.location()
    manager = StorageManager(
        location.project_data,
        storage_locator=locator,
    )
    item_path = location.project_data / "records/kept.json"
    item_path.parent.mkdir(parents=True)
    item_path.write_text("record\n", encoding="utf-8")
    manager.register(
        StorageItem(
            "kept",
            "record",
            str(item_path.relative_to(location.project_data)),
            datetime.now(UTC).isoformat(),
            "completed",
        )
    )

    assert main(
        ["--locale", "zh-CN", "storage", "status", "--root", str(root)]
    ) == 0
    assert "1 个条目，7 字节；0 个活跃，0 个固定。" in capsys.readouterr().out

    assert main(
        ["--locale", "zh-CN", "storage", "prune", "--root", str(root)]
    ) == 0
    assert "0 个条目，0 字节可清理。" in capsys.readouterr().out

    assert main(
        [
            "--locale",
            "zh-CN",
            "storage",
            "pin",
            "kept",
            "--root",
            str(root),
        ]
    ) == 0
    assert "存储固定已规划。" in capsys.readouterr().out


def test_existing_legacy_task_layout_remains_visible_and_prevents_duplicates(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    xdg_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    assert main(["init", "--root", str(root), "--apply", "--yes", "--json"]) == 0
    capsys.readouterr()
    project_data = StorageLocator(root, xdg_data / "harness").location().project_data
    (project_data / "tasks").mkdir(parents=True)
    TaskService(
        root, data_root=xdg_data / "harness", project_data=project_data
    ).start(
        goal="Preserve a pre-worktree-layout task",
        allowed_scope=("src",),
        capability_ids=("project-tests",),
        task_id="legacy-task",
    )

    assert main(
        ["task", "show", "legacy-task", "--root", str(root), "--json"]
    ) == 0
    assert json.loads(capsys.readouterr().out)["task_id"] == "legacy-task"

    assert main(
        [
            "task",
            "start",
            "--id",
            "legacy-task",
            "--goal",
            "Must not duplicate canonical history",
            "--scope",
            "src",
            "--capability",
            "project-tests",
            "--root",
            str(root),
            "--json",
        ]
    ) == 1
    assert "already exists" in json.loads(capsys.readouterr().out)["message"]

    subprocess.run(("git", "-C", str(root), "add", "."), check=True)
    subprocess.run(
        ("git", "-C", str(root), "commit", "-q", "-m", "legacy layout"),
        check=True,
    )
    linked = tmp_path / "legacy-linked"
    subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "worktree",
            "add",
            "-q",
            "-b",
            "legacy-linked",
            str(linked),
        ),
        check=True,
    )

    assert main(
        [
            "task",
            "start",
            "--id",
            "legacy-task",
            "--goal",
            "Keep linked worktree history isolated",
            "--scope",
            "src",
            "--capability",
            "project-tests",
            "--root",
            str(linked),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    primary_location = StorageLocator(root, xdg_data / "harness").location()
    linked_location = StorageLocator(linked, xdg_data / "harness").location()
    assert primary_location.task_data != primary_location.project_data
    assert linked_location.task_data != primary_location.project_data
    assert (
        linked_location.task_data / "tasks/legacy-task/record.json"
    ).is_file()


def test_storage_operation_lock_serializes_clone_writes(tmp_path: Path) -> None:
    root = copy_git_fixture(tmp_path)
    first = StorageLocator(root, tmp_path / "first-data")
    second = StorageLocator(root, tmp_path / "second-data")
    waiting = Event()
    acquired = Event()

    def acquire_second() -> None:
        waiting.set()
        with second.operation_lock():
            acquired.set()

    with first.operation_lock():
        thread = Thread(target=acquire_second)
        thread.start()
        assert waiting.wait(1)
        assert not acquired.wait(0.1)
    thread.join(timeout=1)
    assert acquired.is_set()


def test_task_and_storage_migration_apply_use_the_shared_clone_lock(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    assert main(["init", "--root", str(root), "--apply", "--yes", "--json"]) == 0
    capsys.readouterr()
    entries: list[str] = []

    @contextmanager
    def tracked_lock(self: StorageLocator) -> Any:
        entries.append("enter")
        try:
            yield
        finally:
            entries.append("exit")

    monkeypatch.setattr(StorageLocator, "operation_lock", tracked_lock)

    assert main(
        [
            "task",
            "start",
            "--id",
            "locked-task",
            "--goal",
            "Use the clone lock",
            "--scope",
            "src",
            "--capability",
            "project-tests",
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert entries == ["enter", "exit"]
    entries.clear()
    direct_data = tmp_path / "direct-runtime-data"
    TaskService(root, data_root=direct_data).start(
        goal="Runtime writes also use the clone lock",
        allowed_scope=("src",),
        capability_ids=("project-tests",),
        task_id="direct-locked-task",
    )
    assert entries == ["enter", "exit"]
    assert main(
        [
            "task",
            "cancel",
            "locked-task",
            "--reason",
            "Prepare migration",
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    entries.clear()
    assert main(
        [
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert entries == ["enter", "exit"]


def test_task_and_migration_use_project_data_write_locks(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    xdg_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    assert main(["init", "--root", str(root), "--apply", "--yes", "--json"]) == 0
    capsys.readouterr()
    entries: list[Path] = []

    @contextmanager
    def tracked_lock(project_data: Path) -> Any:
        entries.append(project_data)
        yield

    monkeypatch.setattr(
        "adaptive_harness.core.task_service.project_data_lock", tracked_lock
    )
    monkeypatch.setattr(
        "adaptive_harness.storage.migration.project_data_lock", tracked_lock
    )

    assert main(
        [
            "task",
            "start",
            "--id",
            "locked-project-data",
            "--goal",
            "Use the project data lock",
            "--scope",
            "src",
            "--capability",
            "project-tests",
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    location = StorageLocator(root, xdg_data / "harness").location()
    assert entries == [location.project_data]
    assert main(
        [
            "task",
            "cancel",
            "locked-project-data",
            "--reason",
            "Prepare migration",
            "--root",
            str(root),
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    entries.clear()

    assert main(
        [
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--apply",
            "--yes",
            "--json",
        ]
    ) == 0

    target = StorageLocator(root, xdg_data / "harness").location_for(
        StorageMode.REPOSITORY_LOCAL
    ).project_data
    assert entries == sorted(
        (location.project_data, target), key=lambda path: str(path)
    )


def test_runtime_task_service_rejects_stale_storage_binding(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = copy_git_fixture(tmp_path)
    data_root = tmp_path / "runtime-data"
    assert main(["init", "--root", str(root), "--apply", "--yes", "--json"]) == 0
    capsys.readouterr()
    service = TaskService(root, data_root=data_root)
    service.start(
        goal="Reject stale Runtime reads and writes",
        allowed_scope=("src",),
        capability_ids=("project-tests",),
        task_id="stale-binding",
    )
    StorageLocator(root, data_root).set_mode(StorageMode.REPOSITORY_LOCAL)

    with pytest.raises(ValueError, match="storage placement changed"):
        service.start(
            goal="Reject a stale Runtime storage binding",
            allowed_scope=("src",),
            capability_ids=("project-tests",),
            task_id="another-stale-binding",
        )
    with pytest.raises(ValueError, match="storage placement changed"):
        service.show("stale-binding")


def test_waiting_storage_writer_rejects_binding_after_mode_switch(
    tmp_path: Path,
) -> None:
    root = copy_git_fixture(tmp_path)
    data_root = tmp_path / "runtime-data"
    locator = StorageLocator(root, data_root)
    location = locator.location()
    manager = StorageManager(
        location.project_data,
        storage_locator=locator,
    )
    waiting = Event()
    errors: list[ValueError] = []

    def register() -> None:
        waiting.set()
        try:
            manager.register(
                StorageItem(
                    "late-record",
                    "record",
                    "records/late.json",
                    datetime.now(UTC).isoformat(),
                    "completed",
                )
            )
        except ValueError as error:
            errors.append(error)

    with locator.operation_lock():
        thread = Thread(target=register)
        thread.start()
        assert waiting.wait(1)
        locator.set_mode(StorageMode.REPOSITORY_LOCAL)
    thread.join(timeout=1)

    assert [str(error) for error in errors] == [
        "storage placement changed; recreate storage writer"
    ]
    assert not (location.project_data / "storage-index.json").exists()
    with pytest.raises(ValueError, match="storage placement changed"):
        manager.status()


def test_self_help_is_localized(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        main(["--locale", "zh-CN", "--help"])

    assert "管理独立 Harness 安装" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="0"):
        main(["--locale", "zh-CN", "self", "update", "--help"])

    help_output = capsys.readouterr().out
    assert "选项:" in help_output
    assert "显示此帮助信息并退出" in help_output
    assert "options:" not in help_output


def test_self_error_body_is_localized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "HARNESS_INSTALL_MANIFEST", str(tmp_path / "missing-installation.json")
    )

    assert main(
        ["--locale", "zh-CN", "self", "update", "--version", "0.2.0"]
    ) == 1

    error = capsys.readouterr().err
    assert "独立安装元数据不可用；请使用原包管理器" in error
    assert "use the original" not in error


def test_storage_error_body_is_localized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = copy_git_fixture(tmp_path)

    assert main(
        [
            "--locale",
            "zh-CN",
            "storage",
            "migrate",
            "repository-local",
            "--root",
            str(root),
            "--yes",
        ]
    ) == 1

    error = capsys.readouterr().err
    assert "--yes 需要同时指定 --apply" in error
    assert "requires" not in error


def test_linked_worktrees_share_project_storage_but_isolate_task_records(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_git_fixture(tmp_path)
    linked = tmp_path / "linked"
    xdg_data = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    assert main(["init", "--root", str(root), "--apply", "--yes", "--json"]) == 0
    capsys.readouterr()
    subprocess.run(("git", "-C", str(root), "add", "."), check=True)
    subprocess.run(
        ("git", "-C", str(root), "commit", "-q", "-m", "initialize harness"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(root), "worktree", "add", "-q", "-b", "linked", str(linked)),
        check=True,
    )

    for worktree in (root, linked):
        assert main(
            [
                "task",
                "start",
                "--id",
                "same-task-id",
                "--goal",
                "Isolate worktree task state",
                "--scope",
                "src",
                "--capability",
                "project-tests",
                "--root",
                str(worktree),
                "--json",
            ]
        ) == 0
        capsys.readouterr()

    location_primary = StorageLocator(root, xdg_data / "harness").location()
    location_linked = StorageLocator(linked, xdg_data / "harness").location()
    assert location_primary.project_data == location_linked.project_data
    assert location_primary.task_data != location_linked.task_data
    assert (location_primary.task_data / "tasks/same-task-id/record.json").is_file()
    assert (location_linked.task_data / "tasks/same-task-id/record.json").is_file()


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
