import json
import os
import shutil
from pathlib import Path

import pytest

from adaptive_harness.init import (
    InitializationError,
    Initializer,
    PlanDriftError,
)
from adaptive_harness.schemas import validator_for

FIXTURES = Path(__file__).parents[2] / "fixtures"


def copy_fixture(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return target


def files_under(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_plan_is_read_only_and_contains_all_canonical_files(tmp_path: Path) -> None:
    root = copy_fixture(tmp_path, "python-project")
    before = files_under(root)

    plan = Initializer(root).plan(adapter="codex")

    assert files_under(root) == before
    assert {change.path for change in plan.changes} == {
        ".harness/config.json",
        ".harness/capabilities.json",
        ".harness/modules.lock.json",
        "AGENTS.md",
    }
    capability_change = next(
        change
        for change in plan.changes
        if change.path == ".harness/capabilities.json"
    )
    capability_document = json.loads(capability_change.after)
    assert capability_document["capabilities"][0]["argv"] == [
        "uv",
        "run",
        "pytest",
    ]
    assert "adaptive-harness:start" in plan.diff()


def test_apply_writes_valid_config_and_is_idempotent(tmp_path: Path) -> None:
    root = copy_fixture(tmp_path, "node-project")
    initializer = Initializer(root)
    plan = initializer.plan(adapter="claude-code")

    initializer.apply(plan)

    config = json.loads((root / ".harness/config.json").read_text(encoding="utf-8"))
    capabilities = json.loads(
        (root / ".harness/capabilities.json").read_text(encoding="utf-8")
    )
    modules = json.loads(
        (root / ".harness/modules.lock.json").read_text(encoding="utf-8")
    )
    validator_for("config").validate(config)
    validator_for("capabilities").validate(capabilities)
    validator_for("modules-lock").validate(modules)
    assert capabilities["capabilities"][0]["argv"] == ["pnpm", "test"]
    assert config["adapter"] == {"id": "claude-code", "mode": "enforced"}
    assert (root / "CLAUDE.md").is_file()
    assert (root / ".claude/settings.json").is_file()
    assert initializer.plan(adapter="claude-code").changes == ()


def test_existing_agent_file_is_preserved_around_managed_block(
    tmp_path: Path,
) -> None:
    root = copy_fixture(tmp_path, "python-project")
    original = (root / "AGENTS.md").read_text(encoding="utf-8")
    initializer = Initializer(root)

    initializer.apply(initializer.plan(adapter="codex"))

    rendered = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert rendered.startswith(original)
    assert "adaptive-harness:start" in rendered


def test_generic_adapter_writes_only_canonical_state(tmp_path: Path) -> None:
    root = copy_fixture(tmp_path, "python-project")
    plan = Initializer(root).plan(adapter="generic")

    assert {change.path for change in plan.changes} == {
        ".harness/config.json",
        ".harness/capabilities.json",
        ".harness/modules.lock.json",
    }


def test_apply_refuses_drift_after_user_review(tmp_path: Path) -> None:
    root = copy_fixture(tmp_path, "python-project")
    initializer = Initializer(root)
    plan = initializer.plan(adapter="codex")
    (root / "AGENTS.md").write_text("changed after review\n", encoding="utf-8")

    with pytest.raises(PlanDriftError):
        initializer.apply(plan)

    assert not (root / ".harness/config.json").exists()


def test_commit_failure_restores_every_original_file(tmp_path: Path) -> None:
    root = copy_fixture(tmp_path, "python-project")
    before = files_under(root)
    commits = 0

    def fail_second_commit(source: Path, destination: Path) -> None:
        nonlocal commits
        commits += 1
        if commits == 2:
            raise OSError("simulated commit failure")
        os.replace(source, destination)

    initializer = Initializer(root, committer=fail_second_commit)

    with pytest.raises(InitializationError, match="simulated commit failure"):
        initializer.apply(initializer.plan(adapter="codex"))

    assert files_under(root) == before
    assert initializer.pending_transaction is False


def test_interrupted_transaction_is_detected_and_recovered(tmp_path: Path) -> None:
    root = copy_fixture(tmp_path, "python-project")
    before = files_under(root)
    commits = 0

    def interrupt_second_commit(source: Path, destination: Path) -> None:
        nonlocal commits
        commits += 1
        if commits == 2:
            raise KeyboardInterrupt
        os.replace(source, destination)

    initializer = Initializer(root, committer=interrupt_second_commit)
    with pytest.raises(KeyboardInterrupt):
        initializer.apply(initializer.plan(adapter="codex"))

    assert initializer.pending_transaction is True
    assert Initializer(root).recover() is True
    assert files_under(root) == before
    assert Initializer(root).pending_transaction is False


@pytest.mark.parametrize("adapter", ["unknown", "codex-enforced"])
def test_unknown_or_unverified_adapter_is_rejected(
    tmp_path: Path, adapter: str
) -> None:
    root = copy_fixture(tmp_path, "python-project")

    with pytest.raises(ValueError, match="adapter"):
        Initializer(root).plan(adapter=adapter)
