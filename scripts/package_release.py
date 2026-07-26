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
    files: list[Path] = []
    for path in sorted(bundle_root.rglob("*")):
        if path.is_symlink():
            parser.error("--bundle must not contain symlinks")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            parser.error("--bundle must contain only regular files and directories")
    binary = bundle_root / "harness"
    if binary not in files or binary.stat().st_mode & 0o111 == 0:
        parser.error("--bundle must contain an executable harness file")

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
            for source_path in files:
                relative = source_path.relative_to(bundle_root)
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
