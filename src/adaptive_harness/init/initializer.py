"""Read-only initialization planning and explicit transactional apply."""

from __future__ import annotations

import difflib
import hashlib
import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adaptive_harness import __version__
from adaptive_harness.adapters import adapter_for
from adaptive_harness.adapters.claude_hook import install_settings
from adaptive_harness.adapters.managed import ManagedProjection
from adaptive_harness.core.gateway import (
    ApprovalPolicy,
    Capability,
    ExecutionEnvironment,
    NetworkAccess,
    Reversibility,
    SideEffect,
)
from adaptive_harness.init.transaction import (
    Committer,
    InitializationError,
    PlannedChange,
    RepositoryTransaction,
)
from adaptive_harness.scanners import scan_project
from adaptive_harness.scanners.profile import ProjectProfile, Provenance

_ADAPTER_PROJECTION = {
    "generic": None,
    "codex": "AGENTS.md",
    "claude-code": "CLAUDE.md",
}


@dataclass(frozen=True, slots=True)
class InitializationPlan:
    """Complete user-reviewable initialization candidate."""

    root: Path
    adapter: str
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
                    fromfile=(
                        f"a/{change.path}" if change.before is not None else "/dev/null"
                    ),
                    tofile=f"b/{change.path}",
                )
            )
        return "".join(sections)


class Initializer:
    """Generate deterministic candidates and apply only after explicit review."""

    def __init__(
        self,
        root: Path,
        *,
        committer: Committer | None = None,
    ) -> None:
        self._root = Path(root).resolve()
        if not self._root.is_dir():
            raise ValueError(f"project root is not a directory: {self._root}")
        self._transaction = RepositoryTransaction(
            self._root, committer=committer
        )

    @property
    def pending_transaction(self) -> bool:
        return self._transaction.pending

    def plan(
        self,
        *,
        adapter: str,
        model_profile: str = "unknown-conservative",
    ) -> InitializationPlan:
        if adapter not in _ADAPTER_PROJECTION:
            raise ValueError(f"unsupported or unverified adapter: {adapter}")
        if self.pending_transaction:
            raise InitializationError(
                "recover the unfinished initialization transaction before planning"
            )
        profile = scan_project(self._root)
        client_adapter = adapter_for(adapter, self._root)
        projection = ManagedProjection.agent_instructions()
        projection_path = _ADAPTER_PROJECTION[adapter]
        projected_content: str | None = None
        managed_projections: list[dict[str, str]] = []
        if projection_path is not None:
            existing = _read_optional_text(self._root / projection_path)
            projected_content = projection.render(existing)
            managed_projections.append(
                {
                    "path": projection_path,
                    "version": projection.version,
                    "content_sha256": projection.content_hash,
                }
            )

        documents = {
            ".harness/config.json": _config_document(
                profile,
                adapter=adapter,
                adapter_mode=client_adapter.capability_probe().mode.value,
                model_profile=model_profile,
                managed_projections=managed_projections,
            ),
            ".harness/capabilities.json": _capabilities_document(profile),
            ".harness/modules.lock.json": {
                "schema_version": "1.0",
                "modules": [],
                "templates": [],
                "trials": [],
            },
        }
        candidates: dict[str, bytes] = {
            path: _json_bytes(document) for path, document in documents.items()
        }
        if projection_path is not None and projected_content is not None:
            candidates[projection_path] = projected_content.encode("utf-8")
        if adapter == "claude-code":
            settings = _read_optional_text(self._root / ".claude/settings.json")
            candidates[".claude/settings.json"] = install_settings(settings).encode(
                "utf-8"
            )

        changes: list[PlannedChange] = []
        for path in sorted(candidates):
            target = self._root.joinpath(*Path(path).parts)
            if target.is_symlink():
                raise InitializationError(f"refusing to manage symlink: {path}")
            before = target.read_bytes() if target.is_file() else None
            after = candidates[path]
            if before != after:
                changes.append(PlannedChange(path=path, before=before, after=after))
        return InitializationPlan(
            root=self._root,
            adapter=adapter,
            changes=tuple(changes),
        )

    def apply(self, plan: InitializationPlan) -> None:
        if plan.root != self._root:
            raise ValueError("initialization plan belongs to another project")
        self._transaction.apply(plan.changes)

    def recover(self) -> bool:
        return self._transaction.recover()


def _config_document(
    profile: ProjectProfile,
    *,
    adapter: str,
    adapter_mode: str,
    model_profile: str,
    managed_projections: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "runtime_version": __version__,
        "project_profile": {
            "strategy": "deterministic-local",
            "fingerprint": _profile_fingerprint(profile),
        },
        "model_profile": {"id": model_profile, "version": "1.0"},
        "adapter": {"id": adapter, "mode": adapter_mode},
        "policy": {
            "unknown_operations": "deny_or_approve",
            "telemetry_enabled": False,
        },
        "modules": {
            "activation_policy": "suggest",
            "context_budget_tokens": 2000,
        },
        "feedback": {
            "mode": "minimal",
            "analysis_policy": "on_demand",
            "include_token_usage": True,
            "retention_days": 30,
        },
        "managed_projections": managed_projections,
    }


def _capabilities_document(profile: ProjectProfile) -> dict[str, Any]:
    facts = [
        fact
        for fact in profile.facts_for("test_command")
        if fact.provenance is Provenance.OBSERVED and fact.value is not None
    ]
    capabilities: list[dict[str, Any]] = []
    if facts:
        test_command = facts[0].value
        if test_command is None:
            raise InitializationError("observed test command has no value")
        read_paths = tuple(
            sorted(
                {
                    fact.value
                    for category in ("source_directory", "test_directory")
                    for fact in profile.facts_for(category)
                    if fact.value is not None
                }
            )
        ) or (".",)
        capability = Capability(
            id="project-tests",
            argv=tuple(shlex.split(test_command)),
            cwd=".",
            timeout_seconds=120,
            read_paths=read_paths,
            write_paths=(),
            network=NetworkAccess.NONE,
            listener=False,
            side_effects=(SideEffect.FILESYSTEM_READ,),
            reversibility=Reversibility.REVERSIBLE,
            environment=ExecutionEnvironment.TEST,
            approval_policy=ApprovalPolicy.AUTO,
            max_executions=2,
            stop_on_exit_codes=(0,),
        )
        capabilities.append(capability.to_dict())
    return {"schema_version": "1.0", "capabilities": capabilities}


def _profile_fingerprint(profile: ProjectProfile) -> str:
    facts = [
        {
            "category": fact.category,
            "value": fact.value,
            "provenance": fact.provenance.value,
            "evidence": fact.evidence,
            "confidence": fact.confidence,
        }
        for fact in profile.facts
        if fact.category not in {"agent_file", "harness_file"}
    ]
    canonical = json.dumps(
        facts, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _read_optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise InitializationError(f"managed projection is not a file: {path.name}")
    return path.read_text(encoding="utf-8")


def _json_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


__all__ = ["InitializationPlan", "Initializer"]
