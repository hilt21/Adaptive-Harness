#!/usr/bin/env python3
"""Exercise the public quickstart as an isolated end-to-end smoke test."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast


def run(
    argv: list[str],
    *,
    env: dict[str, str],
    expected_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, capture_output=True, env=env, text=True)
    if result.returncode != expected_exit:
        rendered = " ".join(argv)
        raise RuntimeError(
            f"{rendered} returned {result.returncode}, expected {expected_exit}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def run_json(
    argv: list[str],
    *,
    env: dict[str, str],
    expected_exit: int = 0,
) -> dict[str, Any]:
    result = run(argv, env=env, expected_exit=expected_exit)
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"command did not return JSON: {result.stdout}") from error
    if not isinstance(document, dict):
        raise RuntimeError("command returned a non-object JSON document")
    return cast(dict[str, Any], document)


def module_event(record: dict[str, Any], module_id: str) -> dict[str, Any]:
    for event in record.get("events", []):
        if event.get("type") != "module.activation":
            continue
        data = event.get("data", {})
        if isinstance(data, dict) and data.get("module_id") == module_id:
            return cast(dict[str, Any], data)
    raise AssertionError(f"no activation event found for {module_id}")


def assert_symlink_rejected(
    target: Path,
    *,
    prepare_script: Path,
    env: dict[str, str],
) -> None:
    saved = target.with_name(f"{target.name}.saved")
    target_is_directory = target.is_dir()
    target.rename(saved)
    target.symlink_to(saved, target_is_directory=target_is_directory)
    try:
        rejected = run(
            [str(prepare_script)],
            env=env,
            expected_exit=1,
        )
        assert "is a symlink" in rejected.stderr
    finally:
        target.unlink()
        saved.rename(target)


def main() -> int:
    if not __debug__:
        raise RuntimeError("quickstart smoke assertions must be enabled")

    source_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PATH"] = (
        f"{source_root / '.venv' / 'bin'}{os.pathsep}"
        f"{environment.get('PATH', '')}"
    )
    for command in ("git", "adp-harness", "uv"):
        if shutil.which(command, path=environment["PATH"]) is None:
            raise RuntimeError(f"{command} is required for the quickstart smoke test")

    with tempfile.TemporaryDirectory(prefix="adaptive-harness-quickstart-") as raw:
        sandbox = Path(raw) / "checkout"
        template_target = sandbox / "examples" / "python-quickstart"
        scripts_target = sandbox / "scripts"
        template_target.parent.mkdir(parents=True)
        scripts_target.mkdir(parents=True)
        shutil.copytree(
            source_root / "examples" / "python-quickstart",
            template_target,
        )
        shutil.copy2(
            source_root / "scripts" / "prepare-quickstart.sh",
            scripts_target / "prepare-quickstart.sh",
        )

        run(
            [str(scripts_target / "prepare-quickstart.sh")],
            env=environment,
        )
        repository = sandbox / ".demo" / "python-quickstart" / "repo"
        state = sandbox / ".demo" / "python-quickstart" / "state"
        assert repository.is_dir()
        assert state.is_dir()

        init_plan = run_json(
            [
                "adp-harness",
                "init",
                "--root",
                str(repository),
                "--adapter",
                "generic",
                "--json",
            ],
            env=environment,
        )
        assert init_plan["status"] == "planned"
        assert init_plan["files"] == []
        assert init_plan["diff"] == ""

        task_common = [
            "--root",
            str(repository),
            "--data-root",
            str(state),
            "--json",
        ]
        started = run_json(
            [
                "adp-harness",
                "task",
                "start",
                "--id",
                "demo-fix",
                "--goal",
                "Fix subtraction without changing the test",
                "--scope",
                "src",
                "--scope",
                "tests",
                "--capability",
                "project-tests",
                "--trait",
                "code-change",
                *task_common,
            ],
            env=environment,
        )
        assert started["task_id"] == "demo-fix"
        assert started["state"] == "draft"
        installed = module_event(started, "tdd-guidance")
        assert installed["state"] == "installed"
        assert installed["context"] is None

        failed = run_json(
            [
                "adp-harness",
                "capability",
                "run",
                "--task",
                "demo-fix",
                "--capability",
                "project-tests",
                *task_common,
            ],
            env=environment,
            expected_exit=2,
        )
        failed_artifacts = {
            Path(failed["stdout_artifact"]): Path(
                failed["stdout_artifact"]
            ).read_bytes(),
            Path(failed["stderr_artifact"]): Path(
                failed["stderr_artifact"]
            ).read_bytes(),
        }
        failure_output = b"\n".join(failed_artifacts.values()).decode(
            errors="replace"
        )
        assert failed["status"] == "command_failure"
        assert failed["exit_code"] == 1, failure_output
        assert failed["attempt"] == 1

        implementation = repository / "src" / "quickstart_math.py"
        before = implementation.read_text(encoding="utf-8")
        after = before.replace("return a + b", "return a - b")
        assert before != after
        implementation.write_text(after, encoding="utf-8")

        succeeded = run_json(
            [
                "adp-harness",
                "capability",
                "run",
                "--task",
                "demo-fix",
                "--capability",
                "project-tests",
                *task_common,
            ],
            env=environment,
        )
        assert succeeded["status"] == "succeeded"
        assert succeeded["exit_code"] == 0
        assert succeeded["attempt"] == 2
        succeeded_artifacts = {
            Path(succeeded["stdout_artifact"]),
            Path(succeeded["stderr_artifact"]),
        }
        assert failed_artifacts.keys().isdisjoint(succeeded_artifacts)

        verified = run_json(
            [
                "adp-harness",
                "task",
                "verify",
                "demo-fix",
                *task_common,
            ],
            env=environment,
        )
        assert verified["outcome"] == "completed"
        assert verified["issues"] == []
        assert [item["status"] for item in verified["acceptances"]] == ["passed"]

        completed = run_json(
            [
                "adp-harness",
                "task",
                "show",
                "demo-fix",
                *task_common,
            ],
            env=environment,
        )
        finished = [
            event["data"]["status"]
            for event in completed["events"]
            if event["type"] == "command.finished"
        ]
        assert completed["state"] == "completed"
        assert finished == ["command_failure", "succeeded"]
        assert completed["events"][-1]["type"] == "task.completed"
        for artifact, contents in failed_artifacts.items():
            assert artifact.is_file()
            assert artifact.read_bytes() == contents

        lock_path = repository / ".harness" / "modules.lock.json"
        lock_before_plan = lock_path.read_bytes()
        module_common = ["--root", str(repository), "--json"]
        planned = run_json(
            [
                "adp-harness",
                "module",
                "enable",
                "tdd-guidance",
                "--policy",
                "auto",
                *module_common,
            ],
            env=environment,
        )
        assert planned["status"] == "planned"
        assert planned["files"] == [".harness/modules.lock.json"]
        assert '"activation_policy": "auto"' in planned["diff"]
        assert lock_path.read_bytes() == lock_before_plan

        applied = run_json(
            [
                "adp-harness",
                "module",
                "enable",
                "tdd-guidance",
                "--policy",
                "auto",
                "--apply",
                "--yes",
                *module_common,
            ],
            env=environment,
        )
        assert applied["status"] == "applied"

        modules = run_json(
            [
                "adp-harness",
                "module",
                "list",
                *module_common,
            ],
            env=environment,
        )
        tdd = next(
            item for item in modules["modules"] if item["id"] == "tdd-guidance"
        )
        assert tdd["state"] == "enabled"
        assert tdd["activation_policy"] == "auto"

        run(
            [
                "git",
                "-C",
                str(repository),
                "add",
                "src",
                ".harness/modules.lock.json",
            ],
            env=environment,
        )
        run(
            [
                "git",
                "-C",
                str(repository),
                "commit",
                "-q",
                "-m",
                "Complete quickstart task and enable TDD guidance",
            ],
            env=environment,
        )

        activated_task = run_json(
            [
                "adp-harness",
                "task",
                "start",
                "--id",
                "demo-module",
                "--goal",
                "Demonstrate progressive module activation",
                "--scope",
                "src",
                "--scope",
                "tests",
                "--capability",
                "project-tests",
                "--trait",
                "code-change",
                *task_common,
            ],
            env=environment,
        )
        activated = module_event(activated_task, "tdd-guidance")
        assert activated["state"] == "activated"
        assert activated["context"] == (
            "Write a failing test, implement the smallest fix, then verify it."
        )

        cancelled = run_json(
            [
                "adp-harness",
                "task",
                "cancel",
                "demo-module",
                "--reason",
                "Activation demonstrated",
                *task_common,
            ],
            env=environment,
        )
        assert cancelled["state"] == "abandoned"

        reused = run(
            [str(scripts_target / "prepare-quickstart.sh")],
            env=environment,
        )
        assert "already prepared" in reused.stdout
        assert "return a - b" in implementation.read_text(encoding="utf-8")

        reset = run(
            [str(scripts_target / "prepare-quickstart.sh"), "--reset"],
            env=environment,
        )
        assert "Previous run preserved at" in reset.stdout
        assert "return a + b" in implementation.read_text(encoding="utf-8")
        backups = list((sandbox / ".demo" / "backups").iterdir())
        assert len(backups) == 1
        assert "return a - b" in (
            backups[0] / "repo" / "src" / "quickstart_math.py"
        ).read_text(encoding="utf-8")
        assert any((backups[0] / "state").iterdir())

        prepare_script = scripts_target / "prepare-quickstart.sh"
        run_root = sandbox / ".demo" / "python-quickstart"
        for protected_path in (
            state,
            run_root / ".adaptive-harness-quickstart",
            repository / ".git",
            repository / ".harness" / "capabilities.json",
            repository,
            sandbox / ".demo" / "backups",
        ):
            assert_symlink_rejected(
                protected_path,
                prepare_script=prepare_script,
                env=environment,
            )

        marker = run_root / ".adaptive-harness-quickstart"
        marker_contents = marker.read_bytes()
        marker.write_text("unsupported\n", encoding="utf-8")
        rejected_marker = run(
            [str(prepare_script)],
            env=environment,
            expected_exit=1,
        )
        assert "rerun with --reset" in rejected_marker.stderr
        recovered_marker = run(
            [str(prepare_script), "--reset"],
            env=environment,
        )
        assert "Previous run preserved at" in recovered_marker.stdout
        assert marker.read_bytes() == marker_contents

        git_directory = repository / ".git"
        invalid_git_directory = repository / ".git.invalid"
        git_directory.rename(invalid_git_directory)
        try:
            rejected_git = run(
                [str(prepare_script)],
                env=environment,
                expected_exit=1,
            )
            assert "rerun with --reset" in rejected_git.stderr
        finally:
            invalid_git_directory.rename(git_directory)

        capabilities_path = repository / ".harness" / "capabilities.json"
        capabilities_contents = capabilities_path.read_bytes()
        capabilities_path.unlink()
        try:
            rejected_incomplete = run(
                [str(prepare_script)],
                env=environment,
                expected_exit=1,
            )
            assert "rerun with --reset" in rejected_incomplete.stderr
        finally:
            capabilities_path.write_bytes(capabilities_contents)

        original_head = run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            env=environment,
        ).stdout
        original_source = implementation.read_bytes()
        restore_sentinel = state / "restore-sentinel"
        restore_sentinel.write_text("preserve me\n", encoding="utf-8")

        failing_bin = sandbox / "failing-bin"
        failing_bin.mkdir()
        failing_uv = failing_bin / "uv"
        failing_uv.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
        failing_uv.chmod(0o755)
        failing_environment = dict(environment)
        failing_environment["PATH"] = (
            f"{failing_bin}{os.pathsep}{failing_environment['PATH']}"
        )
        run(
            [str(prepare_script), "--reset"],
            env=failing_environment,
            expected_exit=42,
        )
        restored_head = run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            env=environment,
        ).stdout
        assert restored_head == original_head
        assert implementation.read_bytes() == original_source
        assert restore_sentinel.read_text(encoding="utf-8") == "preserve me\n"

    print("Adaptive Harness quickstart smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
