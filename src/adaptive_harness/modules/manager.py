"""Transactional module enablement and trial lifecycle."""

from __future__ import annotations

import copy
import difflib
import json
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, cast

from jsonschema import ValidationError

from adaptive_harness.init.transaction import (
    InitializationError,
    PlannedChange,
    RepositoryTransaction,
)
from adaptive_harness.modules.model import (
    ActivationDecision,
    ActivationPolicy,
    ActivationState,
    ModuleManifest,
    decide_activation,
)
from adaptive_harness.modules.registry import ModuleRegistry
from adaptive_harness.schemas import validator_for


@dataclass(frozen=True, slots=True)
class ModuleStatus:
    manifest: ModuleManifest
    source: str
    enabled: bool
    activation_policy: ActivationPolicy


@dataclass(frozen=True, slots=True)
class ModulePlan:
    root: Path
    operation: str
    module_id: str
    changes: tuple[PlannedChange, ...]

    def diff(self) -> str:
        sections: list[str] = []
        for change in self.changes:
            before = (
                change.before.decode("utf-8").splitlines(keepends=True)
                if change.before is not None
                else []
            )
            after = change.after.decode("utf-8").splitlines(keepends=True)
            sections.extend(
                difflib.unified_diff(
                    before,
                    after,
                    fromfile=f"a/{change.path}",
                    tofile=f"b/{change.path}",
                )
            )
        return "".join(sections)


class ModuleManager:
    """Manage canonical module locks without copying module source."""

    def __init__(self, root: Path, *, registry: ModuleRegistry | None = None) -> None:
        self.root = Path(root).resolve()
        self.registry = registry or ModuleRegistry()
        self._transaction = RepositoryTransaction(self.root)

    def list(self) -> tuple[ModuleStatus, ...]:
        document = self._load()
        locked = {item["id"]: item for item in document["modules"]}
        statuses: list[ModuleStatus] = []
        for manifest in self.registry.list_builtin():
            item = locked.get(manifest.id)
            policy = ActivationPolicy(
                item.get("activation_policy", manifest.activation_policy.value)
                if item is not None
                else manifest.activation_policy.value
            )
            statuses.append(
                ModuleStatus(
                    manifest,
                    "builtin",
                    bool(item and item.get("enabled", False)),
                    policy,
                )
            )
        for item in document["modules"]:
            if item["source"] != "local":
                continue
            manifest = self._load_locked_local(item)
            statuses.append(
                ModuleStatus(
                    manifest,
                    "local",
                    bool(item.get("enabled", False)),
                    ActivationPolicy(
                        item.get(
                            "activation_policy", manifest.activation_policy.value
                        )
                    ),
                )
            )
        return tuple(statuses)

    def plan_enable(
        self,
        module_id: str,
        *,
        policy: ActivationPolicy | None = None,
        local_manifest: Path | None = None,
    ) -> ModulePlan:
        document = self._load()
        manifest, source, location = self._resolve_manifest(
            module_id, local_manifest=local_manifest
        )
        selected_policy = policy or manifest.activation_policy
        item: dict[str, Any] = {
            "id": manifest.id,
            "version": manifest.version,
            "source": source,
            "sha256": manifest.sha256,
            "enabled": True,
            "activation_policy": selected_policy.value,
        }
        if location is not None:
            item["location"] = location
        _upsert(document["modules"], item)
        return self._plan("enable", manifest.id, document)

    def activation_decisions(
        self,
        *,
        task_traits: frozenset[str],
        available_capabilities: frozenset[str],
        manually_requested: frozenset[str] = frozenset(),
        context_budget_tokens: int = 2000,
    ) -> tuple[ActivationDecision, ...]:
        if context_budget_tokens < 0:
            raise ValueError("module context budget cannot be negative")
        statuses = self.list()
        installed = {status.manifest.id for status in statuses}
        unknown = manually_requested - installed
        if unknown:
            raise ValueError(f"unknown manually requested modules: {sorted(unknown)}")
        remaining = context_budget_tokens
        decisions: list[ActivationDecision] = []
        for status in statuses:
            manifest = replace(
                status.manifest,
                activation_policy=status.activation_policy,
            )
            decision = decide_activation(
                manifest,
                enabled=status.enabled,
                task_traits=task_traits,
                available_capabilities=available_capabilities,
                manually_requested=manifest.id in manually_requested,
                context_budget_tokens=remaining,
            )
            decisions.append(decision)
            if decision.state is ActivationState.ACTIVATED:
                remaining -= manifest.cost.context_tokens
        return tuple(decisions)

    def plan_disable(self, module_id: str) -> ModulePlan:
        document = self._load()
        item = _locked_item(document, module_id)
        item["enabled"] = False
        return self._plan("disable", module_id, document)

    def plan_trial(
        self,
        module_id: str,
        *,
        matching_tasks: int = 3,
    ) -> ModulePlan:
        if matching_tasks < 1:
            raise ValueError("trial must include at least one matching task")
        document = self._load()
        if any(
            item["module_id"] == module_id
            and item["state"] in {"proposed", "approved", "trial", "promoted"}
            for item in document.get("trials", [])
        ):
            raise ValueError(f"module already has an active trial: {module_id}")
        existing = next(
            (item for item in document["modules"] if item["id"] == module_id),
            None,
        )
        previous = copy.deepcopy(existing)
        location: str | None
        if existing is not None and existing["source"] == "local":
            manifest = self._load_locked_local(existing)
            source = "local"
            location = cast(str, existing["location"])
        else:
            manifest, source, location = self._resolve_manifest(module_id)
        locked: dict[str, Any] = {
            "id": manifest.id,
            "version": manifest.version,
            "source": source,
            "sha256": manifest.sha256,
            "enabled": True,
            "activation_policy": manifest.activation_policy.value,
        }
        if location is not None:
            locked["location"] = location
        _upsert(document["modules"], locked)
        trials = document.setdefault("trials", [])
        trials.append(
            {
                "id": f"trial-{module_id}-{len(trials) + 1}",
                "module_id": module_id,
                "state": "trial",
                "remaining_tasks": matching_tasks,
                "matching_tasks": matching_tasks,
                "results": [],
                "previous": previous,
                "success_metrics": list(manifest.success_metrics),
                "cost_budget": {
                    "context_tokens": manifest.cost.context_tokens * matching_tasks,
                    "runtime_ms": int(
                        manifest.cost.runtime_seconds * 1000 * matching_tasks
                    ),
                },
                "control": {
                    "enabled": bool(previous and previous.get("enabled", False)),
                    "activation_policy": (
                        previous.get("activation_policy", "disabled")
                        if previous is not None
                        else "disabled"
                    ),
                },
                "stop_conditions": {
                    "maximum_tasks": matching_tasks,
                    "minimum_beneficial_rate": 0.5,
                },
                "rollback": manifest.rollback,
                "history": ["proposed", "approved", "trial"],
            }
        )
        return self._plan("trial", module_id, document)

    def plan_record_trial_result(
        self,
        module_id: str,
        *,
        task_id: str,
        evidence_ref: str,
        beneficial: bool,
        overhead_ms: int = 0,
    ) -> ModulePlan:
        if not task_id or not evidence_ref:
            raise ValueError("trial result requires task_id and evidence_ref")
        if type(overhead_ms) is not int or overhead_ms < 0:
            raise ValueError("trial overhead_ms must be a non-negative integer")
        document = self._load()
        trial = _active_trial(document, module_id)
        if trial["remaining_tasks"] == 0:
            raise ValueError(f"trial already has all results: {module_id}")
        if any(item["task_id"] == task_id for item in trial["results"]):
            raise ValueError(f"trial already recorded task: {task_id}")
        trial["results"].append(
            {
                "task_id": task_id,
                "evidence_ref": evidence_ref,
                "beneficial": beneficial,
                "overhead_ms": overhead_ms,
            }
        )
        trial["remaining_tasks"] -= 1
        spent = sum(item["overhead_ms"] for item in trial["results"])
        if spent > trial["cost_budget"]["runtime_ms"]:
            trial["state"] = "rejected"
            trial.setdefault("history", []).append("rejected")
        return self._plan("record-trial-result", module_id, document)

    def plan_promote(self, module_id: str) -> ModulePlan:
        document = self._load()
        trial = _active_trial(document, module_id)
        if trial["remaining_tasks"] != 0:
            raise ValueError(f"trial still has matching tasks remaining: {module_id}")
        results = cast(list[dict[str, Any]], trial["results"])
        if sum(bool(item["beneficial"]) for item in results) <= len(results) / 2:
            raise ValueError(f"trial did not demonstrate benefit: {module_id}")
        trial["state"] = "promoted"
        trial.setdefault("history", []).append("promoted")
        _locked_item(document, module_id)["enabled"] = True
        return self._plan("promote", module_id, document)

    def plan_rollback(self, module_id: str) -> ModulePlan:
        document = self._load()
        trial = _latest_trial(document, module_id)
        if trial["state"] in {"rejected", "rolled_back"}:
            raise ValueError(f"trial is already closed: {module_id}")
        previous = trial["previous"]
        document["modules"] = [
            item for item in document["modules"] if item["id"] != module_id
        ]
        if previous is not None:
            document["modules"].append(previous)
        trial["state"] = "rolled_back"
        trial.setdefault("history", []).append("rolled_back")
        return self._plan("rollback", module_id, document)

    def apply(self, plan: ModulePlan) -> None:
        if plan.root != self.root:
            raise ValueError("module plan belongs to another project")
        self._transaction.apply(plan.changes)

    def _resolve_manifest(
        self, module_id: str, *, local_manifest: Path | None = None
    ) -> tuple[ModuleManifest, str, str | None]:
        if local_manifest is None:
            try:
                manifest = self.registry.get_builtin(module_id)
            except KeyError as error:
                raise ValueError(f"unknown builtin module: {module_id}") from error
            manifest.ensure_compatible()
            return manifest, "builtin", None
        manifest_path = Path(local_manifest).resolve()
        try:
            relative = manifest_path.relative_to(self.root)
        except ValueError as error:
            raise ValueError(
                "local module manifest must be inside the repository"
            ) from error
        manifest = self.registry.load_local(manifest_path)
        if manifest.id != module_id:
            raise ValueError("local manifest id does not match requested module")
        return manifest, "local", PurePosixPath(relative).as_posix()

    def _load_locked_local(self, item: dict[str, Any]) -> ModuleManifest:
        location = item.get("location")
        if not isinstance(location, str):
            raise InitializationError(f"local module has no location: {item['id']}")
        path = self.root.joinpath(*PurePosixPath(location).parts)
        manifest = self.registry.load_local(path)
        if manifest.sha256 != item["sha256"]:
            raise InitializationError(f"local module hash mismatch: {item['id']}")
        return manifest

    def _load(self) -> dict[str, Any]:
        path = self.root / ".harness/modules.lock.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise InitializationError(f"module lock is unreadable: {error}") from error
        try:
            validator_for("modules-lock").validate(value)
        except ValidationError as error:
            raise InitializationError(
                f"module lock does not match its schema: {error.message}"
            ) from error
        if not isinstance(value, dict):
            raise InitializationError("module lock root must be an object")
        value.setdefault("trials", [])
        return cast(dict[str, Any], value)

    def _plan(
        self, operation: str, module_id: str, document: dict[str, Any]
    ) -> ModulePlan:
        path = self.root / ".harness/modules.lock.json"
        before = path.read_bytes()
        after = (
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        changes = (
            ()
            if before == after
            else (PlannedChange(".harness/modules.lock.json", before, after),)
        )
        return ModulePlan(self.root, operation, module_id, changes)


def _upsert(items: list[dict[str, Any]], replacement: dict[str, Any]) -> None:
    items[:] = [item for item in items if item["id"] != replacement["id"]]
    items.append(replacement)
    items.sort(key=lambda item: item["id"])


def _locked_item(document: dict[str, Any], module_id: str) -> dict[str, Any]:
    for item in document["modules"]:
        if item["id"] == module_id:
            return cast(dict[str, Any], item)
    raise ValueError(f"module is not enabled or locked: {module_id}")


def _active_trial(document: dict[str, Any], module_id: str) -> dict[str, Any]:
    for trial in reversed(document.get("trials", [])):
        if trial["module_id"] == module_id and trial["state"] == "trial":
            return cast(dict[str, Any], trial)
    raise ValueError(f"module has no active trial: {module_id}")


def _latest_trial(document: dict[str, Any], module_id: str) -> dict[str, Any]:
    for trial in reversed(document.get("trials", [])):
        if trial["module_id"] == module_id:
            return cast(dict[str, Any], trial)
    raise ValueError(f"module has no trial: {module_id}")


__all__ = ["ModuleManager", "ModulePlan", "ModuleStatus"]
