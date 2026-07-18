from pathlib import Path

import pytest

from adaptive_harness.templates import TemplateCatalog


def test_template_plan_is_read_only_and_apply_creates_user_owned_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    catalog = TemplateCatalog(root)

    plan = catalog.plan_render("implementation-plan", "docs/plan.md")

    assert not (root / "docs/plan.md").exists()
    assert "docs/plan.md" in plan.diff()
    catalog.apply(plan)
    rendered = (root / "docs/plan.md").read_text(encoding="utf-8")
    assert rendered.startswith("# Implementation plan")
    assert catalog.plan_render("implementation-plan", "docs/plan.md").changes == ()


def test_existing_template_output_is_only_replaced_after_reviewed_apply(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    output = root / "handoff.md"
    output.write_text("user content\n", encoding="utf-8")
    catalog = TemplateCatalog(root)

    plan = catalog.plan_render("handoff", "handoff.md")

    assert output.read_text(encoding="utf-8") == "user content\n"
    assert "-user content" in plan.diff()
    catalog.apply(plan)
    assert output.read_text(encoding="utf-8").startswith("# Handoff")


@pytest.mark.parametrize(
    "output", ["../outside.md", "/tmp/out.md", ".harness/config.json"]
)
def test_template_cannot_escape_or_modify_harness_state(
    tmp_path: Path, output: str
) -> None:
    with pytest.raises(ValueError):
        TemplateCatalog(tmp_path).plan_render("handoff", output)
