"""Versioned module manifests and activation decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self, cast

from jsonschema import ValidationError

from adaptive_harness import __version__
from adaptive_harness.schemas import validator_for


class ModuleType(StrEnum):
    DECLARATIVE = "declarative"
    EXECUTABLE = "executable"


class ActivationPolicy(StrEnum):
    AUTO = "auto"
    SUGGEST = "suggest"
    MANUAL = "manual"
    DISABLED = "disabled"


class ActivationState(StrEnum):
    INSTALLED = "installed"
    ENABLED = "enabled"
    SUGGESTED = "suggested"
    ACTIVATED = "activated"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ModuleCost:
    context_tokens: int
    runtime_seconds: float


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    id: str
    version: str
    category: str
    module_type: ModuleType
    applies_when: tuple[str, ...]
    excludes_when: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    cost: ModuleCost
    activation_policy: ActivationPolicy
    success_metrics: tuple[str, ...]
    runtime_major: int
    protocol: str
    rollback: str
    content: str | None = None
    entrypoint: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Self:
        try:
            validator_for("module-manifest").validate(document)
        except ValidationError as error:
            raise ValueError(f"invalid module manifest: {error.message}") from error
        cost = cast(dict[str, Any], document["cost"])
        compatibility = cast(dict[str, Any], document["compatibility"])
        return cls(
            id=cast(str, document["id"]),
            version=cast(str, document["version"]),
            category=cast(str, document["category"]),
            module_type=ModuleType(document["type"]),
            applies_when=tuple(cast(list[str], document["applies_when"])),
            excludes_when=tuple(cast(list[str], document["excludes_when"])),
            required_capabilities=tuple(
                cast(list[str], document["required_capabilities"])
            ),
            cost=ModuleCost(
                cast(int, cost["context_tokens"]),
                float(cost["runtime_seconds"]),
            ),
            activation_policy=ActivationPolicy(document["activation_policy"]),
            success_metrics=tuple(cast(list[str], document["success_metrics"])),
            runtime_major=cast(int, compatibility["runtime_major"]),
            protocol=cast(str, compatibility["protocol"]),
            rollback=cast(str, document["rollback"]),
            content=cast(str | None, document.get("content")),
            entrypoint=tuple(cast(list[str], document.get("entrypoint", []))),
        )

    def ensure_compatible(self) -> None:
        runtime_major = int(__version__.split(".", 1)[0])
        if self.runtime_major != runtime_major or self.protocol != "1.0":
            raise ValueError(
                f"module {self.id} is incompatible with runtime {__version__}"
            )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": "1.0",
            "id": self.id,
            "version": self.version,
            "category": self.category,
            "type": self.module_type.value,
            "applies_when": list(self.applies_when),
            "excludes_when": list(self.excludes_when),
            "required_capabilities": list(self.required_capabilities),
            "cost": {
                "context_tokens": self.cost.context_tokens,
                "runtime_seconds": self.cost.runtime_seconds,
            },
            "activation_policy": self.activation_policy.value,
            "success_metrics": list(self.success_metrics),
            "compatibility": {
                "runtime_major": self.runtime_major,
                "protocol": self.protocol,
            },
            "rollback": self.rollback,
        }
        if self.content is not None:
            document["content"] = self.content
        if self.entrypoint:
            document["entrypoint"] = list(self.entrypoint)
        return document


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    module_id: str
    state: ActivationState
    reason: str
    context: str | None = None


def decide_activation(
    manifest: ModuleManifest,
    *,
    enabled: bool,
    task_traits: frozenset[str],
    available_capabilities: frozenset[str],
    manually_requested: bool = False,
    context_budget_tokens: int = 2000,
) -> ActivationDecision:
    """Resolve one module without loading content prematurely."""
    if not enabled:
        return ActivationDecision(manifest.id, ActivationState.INSTALLED, "not enabled")
    if manifest.activation_policy is ActivationPolicy.DISABLED:
        return ActivationDecision(
            manifest.id, ActivationState.ENABLED, "policy disabled"
        )
    if any(item in task_traits for item in manifest.excludes_when):
        return ActivationDecision(
            manifest.id, ActivationState.ENABLED, "task matches an exclusion"
        )
    if not set(manifest.applies_when).issubset(task_traits):
        return ActivationDecision(
            manifest.id, ActivationState.ENABLED, "task conditions do not match"
        )
    missing = set(manifest.required_capabilities) - available_capabilities
    if missing:
        return ActivationDecision(
            manifest.id,
            ActivationState.BLOCKED,
            f"missing capabilities: {', '.join(sorted(missing))}",
        )
    if manifest.cost.context_tokens > context_budget_tokens:
        return ActivationDecision(
            manifest.id, ActivationState.BLOCKED, "context budget exceeded"
        )
    if manifest.activation_policy is ActivationPolicy.SUGGEST:
        return ActivationDecision(
            manifest.id, ActivationState.SUGGESTED, "matching module requires approval"
        )
    if (
        manifest.activation_policy is ActivationPolicy.MANUAL
        and not manually_requested
    ):
        return ActivationDecision(
            manifest.id, ActivationState.ENABLED, "manual activation not requested"
        )
    return ActivationDecision(
        manifest.id,
        ActivationState.ACTIVATED,
        "activation conditions satisfied",
        manifest.content,
    )


__all__ = [
    "ActivationDecision",
    "ActivationPolicy",
    "ActivationState",
    "ModuleCost",
    "ModuleManifest",
    "ModuleType",
    "decide_activation",
]
