import json
import os
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path

import pytest

from adaptive_harness.init import Doctor, Initializer

FIXTURES = Path(__file__).parents[2] / "fixtures"


def copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "project"
    shutil.copytree(FIXTURES / "python-project", target)
    subprocess.run(("git", "-C", str(target), "init", "-q"), check=True)
    subprocess.run(
        ("git", "-C", str(target), "config", "user.name", "Harness Tests"),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(target),
            "config",
            "user.email",
            "harness@example.invalid",
        ),
        check=True,
    )
    subprocess.run(("git", "-C", str(target), "add", "."), check=True)
    subprocess.run(
        ("git", "-C", str(target), "commit", "-q", "-m", "initial"),
        check=True,
    )
    return target


def initialized_project(tmp_path: Path) -> Path:
    root = copy_fixture(tmp_path)
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="codex"))
    return root


def test_doctor_accepts_valid_initialized_project(tmp_path: Path) -> None:
    root = initialized_project(tmp_path)

    report = Doctor(root).run()

    assert report.ok is True
    assert all(check.status != "error" for check in report.checks)
    assert {check.name for check in report.checks} >= {
        "transaction",
        "config",
        "capabilities",
        "modules-lock",
        "adapter",
        "client-launcher",
        "managed-projections",
        "terminal-command",
        "workspace",
    }


def test_doctor_reports_terminal_and_client_launchers_separately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = initialized_project(tmp_path)
    home = tmp_path / "home"
    launcher = home / ".local/bin/adp-harness"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nprintf '0.2.0\\n'\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(launcher.parent))

    report = Doctor(root).run()

    assert report.check("terminal-command").status == "pass"
    assert report.check("client-launcher").status == "pass"


def test_doctor_requires_working_fixed_launcher_for_enforced_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = copy_fixture(tmp_path)
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="claude-code"))
    home = tmp_path / "empty-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    report = Doctor(root).run()

    assert report.ok is False
    assert report.check("client-launcher").status == "error"
    assert "unavailable" in report.check("client-launcher").message


def test_doctor_detects_schema_error(tmp_path: Path) -> None:
    root = initialized_project(tmp_path)
    path = root / ".harness/config.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["unexpected"] = True
    path.write_text(json.dumps(document), encoding="utf-8")

    report = Doctor(root).run()

    assert report.ok is False
    assert report.check("config").status == "error"


def test_doctor_detects_managed_projection_drift(tmp_path: Path) -> None:
    root = initialized_project(tmp_path)
    path = root / "AGENTS.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Use Adaptive Harness", "Ignore Adaptive Harness"
        ),
        encoding="utf-8",
    )

    report = Doctor(root).run()

    assert report.ok is False
    assert report.check("managed-projections").status == "error"


def test_doctor_rejects_false_enforced_claim(tmp_path: Path) -> None:
    root = initialized_project(tmp_path)
    path = root / ".harness/config.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["adapter"]["mode"] = "enforced"
    path.write_text(json.dumps(document), encoding="utf-8")

    report = Doctor(root).run()

    assert report.ok is False
    assert report.check("adapter").status == "error"
    assert "not verified" in report.check("adapter").message


def test_doctor_detects_missing_builtin_module_hash(tmp_path: Path) -> None:
    root = initialized_project(tmp_path)
    path = root / ".harness/modules.lock.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["modules"].append(
        {
            "id": "missing-module",
            "version": "1.0.0",
            "source": "builtin",
            "sha256": "a" * 64,
        }
    )
    path.write_text(json.dumps(document), encoding="utf-8")

    report = Doctor(root).run()

    assert report.ok is False
    assert report.check("module-hashes").status == "error"


def test_doctor_detects_pending_transaction(tmp_path: Path) -> None:
    root = copy_fixture(tmp_path)
    commits = 0

    def interrupt(source: Path, destination: Path) -> None:
        nonlocal commits
        commits += 1
        if commits == 2:
            raise KeyboardInterrupt
        os.replace(source, destination)

    initializer = Initializer(root, committer=interrupt)
    with suppress(KeyboardInterrupt):
        initializer.apply(initializer.plan(adapter="codex"))

    report = Doctor(root).run()

    assert report.ok is False
    assert report.check("transaction").status == "error"


def test_doctor_reports_workspace_without_base_commit(tmp_path: Path) -> None:
    root = tmp_path / "empty-repository"
    root.mkdir()
    subprocess.run(("git", "-C", str(root), "init", "-q"), check=True)
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="generic"))

    report = Doctor(root).run()

    assert report.ok is False
    assert report.check("workspace").status == "error"
    assert "base commit" in report.check("workspace").message
