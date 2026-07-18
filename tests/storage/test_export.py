import json
from pathlib import Path

from adaptive_harness.init import Initializer
from adaptive_harness.storage import ExportManager


def test_export_is_reviewed_and_redacts_sensitive_local_data(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="generic"))
    local = tmp_path / "data/projects/repo-1"
    episode = local / "episodes/episode-1.json"
    episode.parent.mkdir(parents=True)
    episode.write_text(
        json.dumps(
            {
                "task_id": "task-1",
                "path": "/Users/alice/private/project/file.py",
                "remote": "git@example.invalid:private/repo.git",
                "environment": {"API_TOKEN": "top-secret"},
                "message": "token top-secret belongs to alice",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "support-export.json"
    manager = ExportManager(root, local, secrets=("top-secret",))

    plan = manager.plan(output)

    assert not output.exists()
    assert "local/episodes/episode-1.json" in plan.sources
    assert plan.summary()["redactions"]
    manager.apply(plan)
    exported = output.read_text(encoding="utf-8")
    assert "top-secret" not in exported
    assert "/Users/alice" not in exported
    assert "example.invalid" not in exported
    assert '"telemetry": "disabled"' in exported
    assert "[REDACTED]" in exported


def test_export_apply_refuses_output_drift(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    initializer = Initializer(root)
    initializer.apply(initializer.plan(adapter="generic"))
    output = tmp_path / "export.json"
    manager = ExportManager(root, tmp_path / "local")
    plan = manager.plan(output)
    output.write_text("changed", encoding="utf-8")

    try:
        manager.apply(plan)
    except ValueError as error:
        assert "changed after review" in str(error)
    else:
        raise AssertionError("expected export drift rejection")
