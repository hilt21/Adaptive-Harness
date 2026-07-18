"""Deterministic Node.js and TypeScript project scanner."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from adaptive_harness.scanners.profile import ProfileFact, observed


class NodeScanError(RuntimeError):
    """Raised when an observed Node manifest is malformed."""


def scan_node(root: Path) -> list[ProfileFact]:
    manifest_path = root / "package.json"
    if not manifest_path.is_file():
        return []
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise NodeScanError(f"invalid package.json: {error}") from error
    if not isinstance(document, dict):
        raise NodeScanError("invalid package.json: root must be an object")

    facts = [observed("language", "node", "package.json")]
    dependencies = _node_dependencies(document)
    if (root / "tsconfig.json").is_file() or "typescript" in dependencies:
        evidence = (
            "tsconfig.json"
            if (root / "tsconfig.json").is_file()
            else "package.json"
        )
        facts.append(observed("language", "typescript", evidence))

    for dependency, framework in (
        ("next", "next"),
        ("react", "react"),
        ("@nestjs/core", "nestjs"),
        ("express", "express"),
        ("@prisma/client", "prisma"),
    ):
        if dependency in dependencies:
            facts.append(observed("framework", framework, "package.json"))

    manager, lockfile = _node_package_manager(root)
    facts.append(observed("package_manager", manager, lockfile or "package.json"))
    if lockfile is not None:
        facts.append(observed("lockfile", lockfile, lockfile))

    scripts = document.get("scripts", {})
    if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
        command = {
            "pnpm": "pnpm test",
            "yarn": "yarn test",
            "bun": "bun test",
            "npm": "npm test",
        }[manager]
        facts.append(observed("test_command", command, "package.json#scripts.test"))

    for category, candidates in (
        ("source_directory", ("src", "app")),
        ("test_directory", ("tests", "test", "__tests__")),
        ("migration_directory", ("prisma", "migrations")),
    ):
        for directory in candidates:
            if (root / directory).is_dir():
                facts.append(observed(category, directory, directory))

    prisma_schema = root / "prisma" / "schema.prisma"
    if prisma_schema.is_file():
        provider = _prisma_provider(prisma_schema)
        if provider in {"postgresql", "mysql", "sqlite", "mongodb"}:
            facts.append(observed("database", provider, "prisma/schema.prisma"))
    return facts


def _node_dependencies(document: dict[str, Any]) -> set[str]:
    dependencies: set[str] = set()
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        values = document.get(field, {})
        if isinstance(values, dict):
            dependencies.update(
                key for key in values if isinstance(key, str)
            )
    return dependencies


def _node_package_manager(root: Path) -> tuple[str, str | None]:
    for lockfile, manager in (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lock", "bun"),
        ("bun.lockb", "bun"),
        ("package-lock.json", "npm"),
        ("npm-shrinkwrap.json", "npm"),
    ):
        if (root / lockfile).is_file():
            return manager, lockfile
    return "npm", None


def _prisma_provider(path: Path) -> str | None:
    content = path.read_text(encoding="utf-8")
    match = re.search(r'provider\s*=\s*"([A-Za-z0-9_-]+)"', content)
    return match.group(1).lower() if match else None


__all__ = ["NodeScanError", "scan_node"]
