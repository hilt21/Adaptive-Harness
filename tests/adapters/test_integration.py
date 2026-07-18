import json
from pathlib import Path

from adaptive_harness.adapters import IntegrationManager
from adaptive_harness.init import Initializer


def _initialize(root: Path) -> None:
    root.mkdir()
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="generic"))


def test_install_repair_and_uninstall_are_idempotent_and_preserve_user_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _initialize(root)
    original = "# User-owned\nNever remove.\n"
    (root / "AGENTS.md").write_text(original, encoding="utf-8")
    manager = IntegrationManager(root)

    install = manager.plan_install("codex")
    assert "AGENTS.md" in install.diff()
    manager.apply(install)

    config = json.loads((root / ".harness/config.json").read_text(encoding="utf-8"))
    assert config["adapter"] == {"id": "codex", "mode": "observe"}
    assert (root / "AGENTS.md").read_text(encoding="utf-8").startswith(original)
    assert manager.plan_install("codex").changes == ()

    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    (root / "AGENTS.md").write_text(
        agents.replace("Use Adaptive Harness", "Drifted Harness"),
        encoding="utf-8",
    )
    repair = manager.plan_repair("codex")
    assert {change.path for change in repair.changes} == {"AGENTS.md"}
    manager.apply(repair)

    uninstall = manager.plan_uninstall("codex")
    manager.apply(uninstall)
    config = json.loads((root / ".harness/config.json").read_text(encoding="utf-8"))
    assert config["adapter"] == {"id": "generic", "mode": "observe"}
    assert (root / "AGENTS.md").read_text(encoding="utf-8") == original
    assert manager.plan_uninstall("codex").changes == ()


def test_switching_clients_removes_only_previous_managed_block(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _initialize(root)
    (root / "AGENTS.md").write_text("codex user text\n", encoding="utf-8")
    manager = IntegrationManager(root)
    manager.apply(manager.plan_install("codex"))

    manager.apply(manager.plan_install("claude-code"))

    assert (root / "AGENTS.md").read_text(encoding="utf-8") == "codex user text\n"
    assert "adaptive-harness:start" in (root / "CLAUDE.md").read_text(
        encoding="utf-8"
    )


def test_uninstall_absent_projection_does_not_create_file(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _initialize(root)

    plan = IntegrationManager(root).plan_uninstall("codex")

    assert plan.changes == ()
    assert not (root / "AGENTS.md").exists()
