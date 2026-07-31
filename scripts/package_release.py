#!/usr/bin/env python3
"""Create one deterministic standalone release archive and checksum entry."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import tarfile
import tempfile
from pathlib import Path

from adaptive_harness.distribution.version import is_semver

TARGETS = (
    "linux-arm64",
    "linux-x86_64",
    "macos-arm64",
    "macos-x86_64",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--target", required=True, choices=TARGETS)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    if not is_semver(arguments.version):
        parser.error("--version must be a semantic version without a v prefix")
    if arguments.bundle.is_symlink() or not arguments.bundle.is_dir():
        parser.error("--bundle must be a directory, not a symlink")
    bundle_root = arguments.bundle.resolve()
    files: list[tuple[Path, Path]] = []
    visited_directories: set[Path] = set()

    def collect(path: Path, logical_path: Path) -> None:
        if path.is_symlink():
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError) as error:
                parser.error(f"--bundle symlink cannot be resolved: {path}: {error}")
            try:
                resolved.relative_to(bundle_root)
            except ValueError:
                parser.error(
                    f"--bundle symlink escapes the bundle root: {path}"
                )
            if resolved.is_dir():
                if resolved in visited_directories:
                    parser.error(f"--bundle symlink loop detected: {path}")
                visited_directories.add(resolved)
                for child in sorted(resolved.iterdir()):
                    collect(child, logical_path / child.name)
                visited_directories.remove(resolved)
                return
            if not resolved.is_file():
                parser.error(
                    f"--bundle symlink must target a regular file: {path}"
                )
            files.append((logical_path, resolved))
            return
        if path.is_dir():
            if path in visited_directories:
                parser.error(f"--bundle directory loop detected: {path}")
            visited_directories.add(path)
            for child in sorted(path.iterdir()):
                collect(child, logical_path / child.name)
            visited_directories.remove(path)
            return
        if path.is_file():
            files.append((logical_path, path))
            return
        parser.error("--bundle must contain only regular files and directories")

    collect(bundle_root, Path())
    binary = bundle_root / "adp-harness"
    if (
        not any(path == Path("adp-harness") for path, _ in files)
        or binary.stat().st_mode & 0o111 == 0
    ):
        parser.error("--bundle must contain an executable adp-harness file")

    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive_name = (
        f"adaptive-harness-v{arguments.version}-{arguments.target}.tar.gz"
    )
    archive = output / archive_name
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output, prefix=f".{archive_name}-", suffix=".tmp"
    )
    try:
        with (
            os.fdopen(descriptor, "wb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped,
            tarfile.open(mode="w", fileobj=zipped) as archive_bundle,
        ):
            for archive_path, source_path in files:
                relative = archive_path
                member = tarfile.TarInfo(
                    (Path("runtime") / relative).as_posix()
                )
                member.mode = source_path.stat().st_mode & 0o777
                member.uid = 0
                member.gid = 0
                member.uname = "root"
                member.gname = "root"
                member.mtime = 0
                member.size = source_path.stat().st_size
                with source_path.open("rb") as source:
                    archive_bundle.addfile(member, source)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(temporary_name, archive)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_name(f"{archive.name}.sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
