"""Deterministic Python project scanner."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from adaptive_harness.scanners.profile import ProfileFact, observed


class PythonScanError(RuntimeError):
    """Raised when an observed Python manifest is malformed."""


def scan_python(root: Path) -> list[ProfileFact]:
    manifest_path = root / "pyproject.toml"
    legacy_manifests = ("setup.py", "setup.cfg", "requirements.txt", "Pipfile")
    if not manifest_path.is_file() and not any(
        (root / filename).is_file() for filename in legacy_manifests
    ):
        return []

    facts = [observed("language", "python", _manifest_evidence(root))]
    document: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            document = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise PythonScanError(f"invalid pyproject.toml: {error}") from error

    dependencies = _python_dependencies(document)
    for dependency, framework in (
        ("django", "django"),
        ("fastapi", "fastapi"),
        ("flask", "flask"),
    ):
        if dependency in dependencies:
            facts.append(observed("framework", framework, "pyproject.toml"))
    if dependencies & {"asyncpg", "psycopg", "psycopg2"}:
        facts.append(observed("database", "postgresql", "pyproject.toml"))
    if dependencies & {"mysqlclient", "pymysql", "mysql-connector-python"}:
        facts.append(observed("database", "mysql", "pyproject.toml"))

    manager, lockfile = _python_package_manager(root)
    if manager is not None:
        evidence = lockfile or _manifest_evidence(root)
        facts.append(observed("package_manager", manager, evidence))
    if lockfile is not None:
        facts.append(observed("lockfile", lockfile, lockfile))

    if _uses_pytest(document, dependencies, root):
        prefix = {
            "uv": "uv run ",
            "poetry": "poetry run ",
            "pipenv": "pipenv run ",
        }.get(manager or "", "")
        facts.append(
            observed("test_command", f"{prefix}pytest", _manifest_evidence(root))
        )

    _append_directories(root, facts)
    return facts


def _manifest_evidence(root: Path) -> str:
    if (root / "pyproject.toml").is_file():
        return "pyproject.toml"
    for filename in ("setup.py", "setup.cfg", "requirements.txt", "Pipfile"):
        if (root / filename).is_file():
            return filename
    return "python manifest"


def _python_dependencies(document: dict[str, Any]) -> set[str]:
    raw: list[str] = []
    project = document.get("project", {})
    if isinstance(project, dict):
        dependencies = project.get("dependencies", [])
        if isinstance(dependencies, list):
            raw.extend(item for item in dependencies if isinstance(item, str))
        optional = project.get("optional-dependencies", {})
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    raw.extend(item for item in group if isinstance(item, str))
    return {_normalize_python_requirement(item) for item in raw}


def _normalize_python_requirement(value: str) -> str:
    name = re.split(r"[<>=!~;\[\s]", value, maxsplit=1)[0]
    return name.lower().replace("_", "-")


def _python_package_manager(root: Path) -> tuple[str | None, str | None]:
    for lockfile, manager in (
        ("uv.lock", "uv"),
        ("poetry.lock", "poetry"),
        ("Pipfile.lock", "pipenv"),
    ):
        if (root / lockfile).is_file():
            return manager, lockfile
    if (root / "pyproject.toml").is_file() or (root / "requirements.txt").is_file():
        return "pip", None
    return None, None


def _uses_pytest(
    document: dict[str, Any], dependencies: set[str], root: Path
) -> bool:
    tool = document.get("tool", {})
    configured = (
        isinstance(tool, dict)
        and isinstance(tool.get("pytest"), dict)
    )
    return configured or "pytest" in dependencies or (root / "pytest.ini").is_file()


def _append_directories(root: Path, facts: list[ProfileFact]) -> None:
    for category, candidates in (
        ("source_directory", ("src",)),
        ("test_directory", ("tests", "test")),
        ("migration_directory", ("migrations", "alembic")),
    ):
        for directory in candidates:
            if (root / directory).is_dir():
                facts.append(observed(category, directory, directory))


__all__ = ["PythonScanError", "scan_python"]

