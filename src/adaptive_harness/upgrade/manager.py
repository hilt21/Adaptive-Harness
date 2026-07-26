"""Compatible, transactional configuration upgrades with local recovery points."""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from jsonschema import ValidationError

from adaptive_harness import __version__
from adaptive_harness.adapters.managed import ManagedProjection
from adaptive_harness.init.transaction import (
    InitializationError,
    PlannedChange,
    RepositoryTransaction,
)
from adaptive_harness.schemas import validator_for
from adaptive_harness.storage.location import (
    StorageLocator,
    bound_project_data_lock,
    resolve_project_data,
)

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class UpgradeStatus:
    configured_version: str
    runtime_version: str
    compatible: bool
    needs_upgrade: bool
    message: str


@dataclass(frozen=True, slots=True)
class UpgradePlan:
    root: Path
    operation: str
    recovery_id: str
    changes: tuple[PlannedChange, ...]
    risks: tuple[str, ...]
    module_changes: tuple[str, ...]

    def diff(self) -> str:
        sections: list[str] = []
        for change in self.changes:
            sections.extend(
                difflib.unified_diff(
                    change.before.decode("utf-8").splitlines(keepends=True)
                    if change.before is not None
                    else [],
                    change.after.decode("utf-8").splitlines(keepends=True),
                    fromfile=f"a/{change.path}",
                    tofile=f"b/{change.path}",
                )
            )
        return "".join(sections)


def check_runtime_version(configured: str) -> UpgradeStatus:
    """Compare one configured runtime against the installed runtime."""
    configured_tuple = _version_tuple(configured)
    runtime_tuple = _version_tuple(__version__)
    if configured_tuple[0] != runtime_tuple[0]:
        return UpgradeStatus(
            configured,
            __version__,
            False,
            configured_tuple < runtime_tuple,
            "runtime major version is incompatible; no migration is registered",
        )
    if configured_tuple > runtime_tuple:
        return UpgradeStatus(
            configured,
            __version__,
            False,
            False,
            "configuration was written by a newer runtime",
        )
    return UpgradeStatus(
        configured,
        __version__,
        True,
        configured_tuple < runtime_tuple,
        (
            "compatible upgrade is available"
            if configured_tuple < runtime_tuple
            else "configuration is current"
        ),
    )


class UpgradeManager:
    """Migrate known configuration versions and preserve exact rollback bytes."""

    def __init__(
        self,
        root: Path,
        data_root: Path | None = None,
        repository_id: str | None = None,
        *,
        project_data: Path | None = None,
        storage_locator: StorageLocator | None = None,
        force_user_data: bool = False,
    ) -> None:
        self.root = Path(root).resolve()
        project_root = resolve_project_data(data_root, repository_id, project_data)
        self.project_root = project_root
        self._storage_locator = storage_locator
        self._force_user_data = force_user_data
        self.recovery_root = project_root / "upgrades"
        self._transaction = RepositoryTransaction(self.root)

    def check(self) -> UpgradeStatus:
        document = self._load_config()
        return check_runtime_version(cast(str, document["runtime_version"]))

    def plan(self) -> UpgradePlan:
        status = self.check()
        if not status.compatible:
            raise InitializationError(status.message)
        config = self._load_config()
        config["runtime_version"] = __version__
        candidates: dict[str, bytes] = {
            ".harness/config.json": _json_bytes(config)
        }
        projection = ManagedProjection.agent_instructions()
        for item in config["managed_projections"]:
            path = cast(str, item["path"])
            target = self.root / path
            if not target.is_file() or target.is_symlink():
                raise InitializationError(
                    f"repair managed projection before upgrade: {path}"
                )
            candidates[path] = projection.render(
                target.read_text(encoding="utf-8")
            ).encode("utf-8")
        changes = self._changes(candidates)
        digest = hashlib.sha256(
            b"".join(change.after for change in changes)
        ).hexdigest()[:12]
        recovery_id = f"upgrade-{__version__}-{digest}"
        risks = (
            "canonical files change transactionally; local recovery point retained",
        )
        return UpgradePlan(
            self.root, "upgrade", recovery_id, changes, risks, ()
        )

    def apply(self, plan: UpgradePlan) -> None:
        with bound_project_data_lock(
            self.project_root,
            self._storage_locator,
            force_user_data=self._force_user_data,
        ):
            if plan.root != self.root or plan.operation != "upgrade":
                raise ValueError(
                    "upgrade plan belongs to another operation or project"
                )
            if not plan.changes:
                return
            recovery = {
                "schema_version": "1.0",
                "id": plan.recovery_id,
                "created_at": datetime.now(UTC).isoformat(),
                "rolled_back": False,
                "changes": [
                    {
                        "path": change.path,
                        "before": (
                            base64.b64encode(change.before).decode("ascii")
                            if change.before is not None
                            else None
                        ),
                        "after_sha256": hashlib.sha256(change.after).hexdigest(),
                    }
                    for change in plan.changes
                ],
            }
            self.recovery_root.mkdir(parents=True, exist_ok=True)
            _atomic_json(
                self.recovery_root / f"{plan.recovery_id}.json", recovery
            )
            self._transaction.apply(plan.changes)

    def plan_rollback(self, recovery_id: str | None = None) -> UpgradePlan:
        with bound_project_data_lock(
            self.project_root,
            self._storage_locator,
            force_user_data=self._force_user_data,
        ):
            path = self._recovery_path(recovery_id)
            recovery = _load_json_object(path)
            if recovery.get("rolled_back") is True:
                raise ValueError("upgrade recovery point was already rolled back")
            changes: list[PlannedChange] = []
            for item in recovery.get("changes", []):
                if not isinstance(item, dict) or not isinstance(
                    item.get("path"), str
                ):
                    raise ValueError("upgrade recovery point is invalid")
                before_encoded = item.get("before")
                if not isinstance(before_encoded, str):
                    raise ValueError(
                        "rollback of newly created files is unsupported"
                    )
                restored = base64.b64decode(before_encoded, validate=True)
                target = self.root / item["path"]
                current = target.read_bytes() if target.is_file() else None
                if (
                    current is None
                    or hashlib.sha256(current).hexdigest()
                    != item.get("after_sha256")
                ):
                    raise ValueError(
                        f"upgraded file changed before rollback: {item['path']}"
                    )
                if current != restored:
                    changes.append(
                        PlannedChange(item["path"], current, restored)
                    )
            return UpgradePlan(
                self.root,
                "rollback",
                cast(str, recovery["id"]),
                tuple(changes),
                ("rollback restores the exact pre-upgrade bytes",),
                (),
            )

    def apply_rollback(self, plan: UpgradePlan) -> None:
        with bound_project_data_lock(
            self.project_root,
            self._storage_locator,
            force_user_data=self._force_user_data,
        ):
            if plan.root != self.root or plan.operation != "rollback":
                raise ValueError(
                    "rollback plan belongs to another operation or project"
                )
            self._transaction.apply(plan.changes)
            path = self._recovery_path(plan.recovery_id)
            recovery = _load_json_object(path)
            recovery["rolled_back"] = True
            _atomic_json(path, recovery)

    def _changes(self, candidates: dict[str, bytes]) -> tuple[PlannedChange, ...]:
        changes: list[PlannedChange] = []
        for path, after in sorted(candidates.items()):
            target = self.root / path
            before = target.read_bytes() if target.is_file() else None
            if before != after:
                changes.append(PlannedChange(path, before, after))
        return tuple(changes)

    def _load_config(self) -> dict[str, Any]:
        path = self.root / ".harness/config.json"
        value = _load_json_object(path)
        try:
            validator_for("config").validate(value)
        except ValidationError as error:
            raise InitializationError(
                f"Harness config does not match its schema: {error.message}"
            ) from error
        _version_tuple(cast(str, value["runtime_version"]))
        return value

    def _recovery_path(self, recovery_id: str | None) -> Path:
        if recovery_id is not None:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", recovery_id):
                raise ValueError("recovery id is unsafe")
            path = self.recovery_root / f"{recovery_id}.json"
            if not path.is_file():
                raise ValueError(f"unknown recovery point: {recovery_id}")
            return path
        candidates = sorted(self.recovery_root.glob("upgrade-*.json"))
        if not candidates:
            raise ValueError("no upgrade recovery point is available")
        return candidates[-1]


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise InitializationError(f"invalid runtime semantic version: {value}")
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def _json_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InitializationError(
            f"JSON document is unreadable: {path.name}"
        ) from error
    if not isinstance(value, dict):
        raise InitializationError(f"JSON document root is invalid: {path.name}")
    return cast(dict[str, Any], value)


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    content = _json_bytes(document)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "UpgradeManager",
    "UpgradePlan",
    "UpgradeStatus",
    "check_runtime_version",
]
