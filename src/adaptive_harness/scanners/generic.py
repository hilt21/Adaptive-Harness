"""Client- and language-independent deterministic project scanning."""

from __future__ import annotations

from pathlib import Path

from adaptive_harness.scanners.profile import ProfileFact, observed


def scan_generic(root: Path) -> list[ProfileFact]:
    facts: list[ProfileFact] = []
    if (root / ".git").exists():
        facts.append(observed("vcs", "git", ".git"))

    workflow_dir = root / ".github" / "workflows"
    if workflow_dir.is_dir() and any(
        path.suffix in {".yml", ".yaml"} for path in workflow_dir.iterdir()
    ):
        facts.append(observed("ci", "github-actions", ".github/workflows"))
    for path, value in (
        (".gitlab-ci.yml", "gitlab-ci"),
        (".circleci/config.yml", "circleci"),
        ("Jenkinsfile", "jenkins"),
    ):
        if (root / path).is_file():
            facts.append(observed("ci", value, path))

    for filename in ("AGENTS.md", "CLAUDE.md"):
        if (root / filename).is_file():
            facts.append(observed("agent_file", filename, filename))
    for filename in ("config.json", "capabilities.json", "modules.lock.json"):
        relative = Path(".harness") / filename
        if (root / relative).is_file():
            facts.append(observed("harness_file", str(relative), str(relative)))
    return facts


__all__ = ["scan_generic"]

