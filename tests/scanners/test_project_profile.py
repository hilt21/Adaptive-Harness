from pathlib import Path

import pytest

from adaptive_harness.scanners import scan_project
from adaptive_harness.scanners.profile import (
    ProfileFact,
    ProfileInvariantError,
    ProjectProfile,
    Provenance,
)

FIXTURES = Path(__file__).parents[2] / "fixtures"


def observed_values(profile: ProjectProfile, category: str) -> set[str]:
    facts = profile.facts_for(category)
    assert all(fact.provenance is Provenance.OBSERVED for fact in facts)
    assert all(fact.evidence for fact in facts)
    return {fact.value for fact in facts if fact.value is not None}


def test_python_scanner_records_observed_profile_facts() -> None:
    profile = scan_project(FIXTURES / "python-project")

    assert observed_values(profile, "language") == {"python"}
    assert observed_values(profile, "package_manager") == {"uv"}
    assert observed_values(profile, "lockfile") == {"uv.lock"}
    assert observed_values(profile, "framework") == {"fastapi"}
    assert observed_values(profile, "database") == {"postgresql"}
    assert observed_values(profile, "test_command") == {"uv run pytest"}
    assert observed_values(profile, "source_directory") == {"src"}
    assert observed_values(profile, "test_directory") == {"tests"}
    assert observed_values(profile, "migration_directory") == {"migrations"}
    assert observed_values(profile, "ci") == {"github-actions"}
    assert observed_values(profile, "agent_file") == {"AGENTS.md"}


def test_node_scanner_records_typescript_package_and_frameworks() -> None:
    profile = scan_project(FIXTURES / "node-project")

    assert observed_values(profile, "language") == {"node", "typescript"}
    assert observed_values(profile, "package_manager") == {"pnpm"}
    assert observed_values(profile, "lockfile") == {"pnpm-lock.yaml"}
    assert observed_values(profile, "framework") == {"next", "react", "prisma"}
    assert observed_values(profile, "database") == {"postgresql"}
    assert observed_values(profile, "test_command") == {"pnpm test"}
    assert observed_values(profile, "source_directory") == {"src"}
    assert observed_values(profile, "test_directory") == {"tests"}
    assert observed_values(profile, "migration_directory") == {"prisma"}


def test_missing_test_command_is_explicitly_unknown(tmp_path: Path) -> None:
    profile = scan_project(tmp_path)

    facts = profile.facts_for("test_command")
    assert len(facts) == 1
    assert facts[0].provenance is Provenance.UNKNOWN
    assert facts[0].value is None
    assert facts[0].evidence == "no supported test command detected"


def test_scan_is_deterministic() -> None:
    path = FIXTURES / "node-project"

    assert scan_project(path) == scan_project(path)


def test_inferred_fact_requires_confidence_and_evidence() -> None:
    with pytest.raises(ProfileInvariantError, match="confidence"):
        ProfileFact(
            category="framework",
            value="unknown-framework",
            provenance=Provenance.INFERRED,
            evidence="model analysis",
        )


def test_observed_fact_requires_evidence() -> None:
    with pytest.raises(ProfileInvariantError, match="evidence"):
        ProfileFact(
            category="language",
            value="python",
            provenance=Provenance.OBSERVED,
            evidence="",
        )
