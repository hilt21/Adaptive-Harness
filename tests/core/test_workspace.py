import subprocess
from pathlib import Path

import pytest

from adaptive_harness.core.workspace import GitWorkspace, WorkspaceProbeError


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(path), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def initialize_repository(path: Path, *, commit: bool = True) -> None:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Harness Tests")
    git(path, "config", "user.email", "harness@example.invalid")
    if commit:
        (path / "README.md").write_text("initial\n", encoding="utf-8")
        git(path, "add", "README.md")
        git(path, "commit", "-q", "-m", "initial")


def test_workspace_snapshot_uses_full_head_and_canonical_worktree(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    initialize_repository(repository)

    facts = GitWorkspace(repository / ".").inspect()

    assert facts.snapshot.base_sha == git(repository, "rev-parse", "HEAD")
    assert len(facts.snapshot.base_sha) == 40
    assert facts.snapshot.worktree_path == repository.resolve()
    assert facts.snapshot.repository_id.startswith("git-")
    assert facts.dirty_state == ()
    assert facts.changed_paths == ()


def test_workspace_reports_tracked_staged_and_untracked_paths(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    initialize_repository(repository)
    base_sha = git(repository, "rev-parse", "HEAD")
    (repository / "README.md").write_text("modified\n", encoding="utf-8")
    (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
    (repository / "untracked file.txt").write_text("new\n", encoding="utf-8")
    git(repository, "add", "staged.txt")

    workspace = GitWorkspace(repository)
    facts = workspace.inspect(base_sha=base_sha)

    assert set(facts.changed_paths) == {
        "README.md",
        "staged.txt",
        "untracked file.txt",
    }
    assert len(facts.dirty_state) == 3


def test_repository_identity_is_shared_by_linked_worktrees(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    linked = tmp_path / "linked"
    initialize_repository(repository)
    git(repository, "worktree", "add", "-q", "-b", "feature", str(linked))

    primary = GitWorkspace(repository).snapshot()
    secondary = GitWorkspace(linked).snapshot()

    assert primary.repository_id == secondary.repository_id
    assert primary.worktree_path != secondary.worktree_path


def test_repository_identity_does_not_change_with_worktree_content(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    initialize_repository(repository)
    workspace = GitWorkspace(repository)
    before = workspace.snapshot().repository_id
    (repository / "new.txt").write_text("new\n", encoding="utf-8")

    assert workspace.snapshot().repository_id == before


def test_repository_without_base_commit_is_not_task_ready(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    initialize_repository(repository, commit=False)

    with pytest.raises(WorkspaceProbeError, match="base commit"):
        GitWorkspace(repository).snapshot()


def test_non_repository_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "plain"
    directory.mkdir()

    with pytest.raises(WorkspaceProbeError, match="Git worktree"):
        GitWorkspace(directory).snapshot()


def test_changed_paths_rejects_unknown_base_sha(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    initialize_repository(repository)

    with pytest.raises(WorkspaceProbeError, match="diff"):
        GitWorkspace(repository).changed_paths("f" * 40)

