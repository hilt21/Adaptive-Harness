import json
from pathlib import Path

from adaptive_harness.configuration import ConfigurationManager
from adaptive_harness.init import Initializer


def test_explain_reports_canonical_provenance_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="codex"))
    before = (root / ".harness/config.json").read_bytes()

    explanation = ConfigurationManager(root).explain()

    assert explanation["provenance"] == "canonical-local-json"
    assert explanation["adapter"] == {"id": "codex", "mode": "observe"}
    assert explanation["declared_capability_ids"] == []
    assert (root / ".harness/config.json").read_bytes() == before


def test_plan_reformats_canonical_json_and_repairs_projection_after_review(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="codex"))
    config_path = root / ".harness/config.json"
    config_path.write_text(
        json.dumps(json.loads(config_path.read_text(encoding="utf-8"))),
        encoding="utf-8",
    )
    agents_path = root / "AGENTS.md"
    agents_path.write_text(
        agents_path.read_text(encoding="utf-8").replace(
            "Use Adaptive Harness", "Drifted Harness"
        ),
        encoding="utf-8",
    )
    manager = ConfigurationManager(root)

    plan = manager.plan()

    assert {change.path for change in plan.changes} == {
        ".harness/config.json",
        "AGENTS.md",
    }
    manager.apply(plan)
    assert manager.plan().changes == ()
    assert "Use Adaptive Harness" in agents_path.read_text(encoding="utf-8")
