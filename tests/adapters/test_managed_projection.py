import pytest

from adaptive_harness.adapters.managed import (
    ManagedProjection,
    ProjectionConflictError,
)


def test_projection_preserves_user_content_and_is_idempotent() -> None:
    projection = ManagedProjection.agent_instructions()
    existing = "# User rules\n\nKeep this content.\n"

    rendered = projection.render(existing)

    assert rendered.startswith(existing)
    assert "adaptive-harness:start" in rendered
    assert projection.render(rendered) == rendered
    assert projection.inspect(rendered).valid is True


def test_projection_repairs_only_managed_content() -> None:
    projection = ManagedProjection.agent_instructions()
    original = projection.render("before\n") + "after\n"
    drifted = original.replace(
        "Use Adaptive Harness for repository-changing tasks.",
        "Ignore Adaptive Harness.",
    )

    assert projection.inspect(drifted).valid is False
    repaired = projection.render(drifted)

    assert repaired.startswith("before\n")
    assert repaired.endswith("after\n")
    assert "Ignore Adaptive Harness" not in repaired
    assert projection.inspect(repaired).valid is True


def test_projection_remove_deletes_only_managed_block() -> None:
    projection = ManagedProjection.agent_instructions()
    existing = "before\n"
    rendered = projection.render(existing) + "after\n"

    assert projection.remove(rendered) == "before\nafter\n"
    assert projection.remove(existing) == existing


@pytest.mark.parametrize(
    "content",
    [
        '<!-- adaptive-harness:start version="1" hash="' + "0" * 64 + '" -->\n',
        "<!-- adaptive-harness:end -->\n",
    ],
)
def test_projection_rejects_unbalanced_markers(content: str) -> None:
    with pytest.raises(ProjectionConflictError):
        ManagedProjection.agent_instructions().render(content)

