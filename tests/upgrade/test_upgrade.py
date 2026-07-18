import json
from pathlib import Path

import pytest

from adaptive_harness import __version__
from adaptive_harness.init import InitializationError, Initializer
from adaptive_harness.upgrade import UpgradeManager


def _initialized(root: Path, adapter: str = "generic") -> None:
    root.mkdir()
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter=adapter))


def test_compatible_upgrade_creates_recovery_and_exact_rollback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _initialized(root, "codex")
    config_path = root / ".harness/config.json"
    original = json.loads(config_path.read_text(encoding="utf-8"))
    original["runtime_version"] = "0.0.0"
    config_path.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    original_bytes = config_path.read_bytes()
    manager = UpgradeManager(root, tmp_path / "data", "repo-1")

    status = manager.check()
    plan = manager.plan()

    assert status.compatible is True
    assert status.needs_upgrade is True
    assert '"runtime_version": "0.1.0"' in plan.diff()
    manager.apply(plan)
    upgraded = json.loads(config_path.read_text(encoding="utf-8"))
    assert upgraded["runtime_version"] == __version__
    assert (manager.recovery_root / f"{plan.recovery_id}.json").is_file()

    rollback = manager.plan_rollback(plan.recovery_id)
    manager.apply_rollback(rollback)
    assert config_path.read_bytes() == original_bytes
    with pytest.raises(ValueError, match="already"):
        manager.plan_rollback(plan.recovery_id)


def test_newer_or_incompatible_runtime_blocks_upgrade(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _initialized(root)
    config_path = root / ".harness/config.json"
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["runtime_version"] = "1.0.0"
    config_path.write_text(json.dumps(document), encoding="utf-8")
    manager = UpgradeManager(root, tmp_path / "data", "repo-1")

    assert manager.check().compatible is False
    with pytest.raises(InitializationError, match="incompatible"):
        manager.plan()


def test_rollback_refuses_post_upgrade_drift(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _initialized(root)
    config_path = root / ".harness/config.json"
    document = json.loads(config_path.read_text(encoding="utf-8"))
    document["runtime_version"] = "0.0.0"
    config_path.write_text(json.dumps(document), encoding="utf-8")
    manager = UpgradeManager(root, tmp_path / "data", "repo-1")
    plan = manager.plan()
    manager.apply(plan)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("minimal", "off"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="changed"):
        manager.plan_rollback(plan.recovery_id)
