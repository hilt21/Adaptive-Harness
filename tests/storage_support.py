"""Test support for storage writers that require a clone binding."""

from __future__ import annotations

import subprocess
from pathlib import Path

from adaptive_harness.storage import StorageLocator


def committed_storage_locator(root: Path, data_root: Path) -> StorageLocator:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(("git", "-C", str(root), "init", "-q"), check=True)
    subprocess.run(
        ("git", "-C", str(root), "config", "user.name", "Harness Tests"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(root), "config", "user.email", "harness@example.com"),
        check=True,
    )
    marker = root / ".storage-test"
    marker.write_text("storage test\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(root), "add", "."), check=True)
    subprocess.run(
        ("git", "-C", str(root), "commit", "-qm", "storage test fixture"),
        check=True,
    )
    return StorageLocator(root, data_root)


__all__ = ["committed_storage_locator"]
