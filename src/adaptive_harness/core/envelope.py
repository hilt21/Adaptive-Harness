"""Task Envelope data model and revision invariants."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Self

_FULL_GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ENVELOPE_FIELDS = {
    "schema_version",
    "task_id",
    "repository_id",
    "base_sha",
    "worktree_path",
    "initial_dirty_state",
    "harness_version",
    "config_version",
    "goal",
    "non_goals",
    "requirements",
    "allowed_scope",
    "acceptances",
    "requested_capabilities",
    "timeout_seconds",
    "retry_budget",
    "budget",
    "integration_mode",
    "revision",
}


class EnvelopeInvariantError(ValueError):
    """Raised when a Task Envelope violates a kernel invariant."""


@dataclass(frozen=True, slots=True)
class Requirement:
    """A requirement that must be covered by acceptance evidence."""

    id: str
    text: str

    def __post_init__(self) -> None:
        _validate_identifier("requirement id", self.id)
        if not isinstance(self.text, str) or not self.text.strip():
            raise EnvelopeInvariantError("requirement text must not be empty")

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "text": self.text}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        _require_exact_fields(value, {"id", "text"}, "requirement")
        return cls(id=value["id"], text=value["text"])


@dataclass(frozen=True, slots=True)
class Acceptance:
    """A structured acceptance check declared by the task."""

    id: str
    required: bool
    covers: tuple[str, ...]
    command_id: str
    expected_exit_code: int
    timeout_seconds: int
    retry_budget: int
    evidence_type: str

    def __post_init__(self) -> None:
        _validate_identifier("acceptance id", self.id)
        _validate_identifier("command_id", self.command_id)
        if type(self.required) is not bool:
            raise EnvelopeInvariantError("acceptance required must be a boolean")
        if not isinstance(self.covers, tuple) or not self.covers:
            raise EnvelopeInvariantError("acceptance covers must not be empty")
        for requirement_id in self.covers:
            _validate_identifier("covered requirement id", requirement_id)
        _reject_duplicates("covered requirement", self.covers)
        _validate_integer("acceptance expected exit_code", self.expected_exit_code)
        _validate_positive_integer(
            "acceptance timeout_seconds", self.timeout_seconds
        )
        _validate_non_negative_integer(
            "acceptance retry_budget", self.retry_budget
        )
        if not isinstance(self.evidence_type, str) or not self.evidence_type.strip():
            raise EnvelopeInvariantError("acceptance evidence_type must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "required": self.required,
            "covers": list(self.covers),
            "command_id": self.command_id,
            "expected": {"exit_code": self.expected_exit_code},
            "timeout_seconds": self.timeout_seconds,
            "retry_budget": self.retry_budget,
            "evidence_type": self.evidence_type,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        expected = {
            "id",
            "required",
            "covers",
            "command_id",
            "expected",
            "timeout_seconds",
            "retry_budget",
            "evidence_type",
        }
        _require_exact_fields(value, expected, "acceptance")
        expected_value = value["expected"]
        if not isinstance(expected_value, dict):
            raise EnvelopeInvariantError("acceptance expected must be an object")
        _require_exact_fields(
            expected_value, {"exit_code"}, "acceptance expected"
        )
        return cls(
            id=value["id"],
            required=value["required"],
            covers=tuple(value["covers"]),
            command_id=value["command_id"],
            expected_exit_code=expected_value["exit_code"],
            timeout_seconds=value["timeout_seconds"],
            retry_budget=value["retry_budget"],
            evidence_type=value["evidence_type"],
        )


@dataclass(frozen=True, slots=True)
class TaskEnvelope:
    """Immutable snapshot of a task's governed boundaries."""

    schema_version: str
    task_id: str
    repository_id: str
    base_sha: str
    worktree_path: Path
    initial_dirty_state: tuple[str, ...]
    harness_version: str
    config_version: str
    goal: str
    non_goals: tuple[str, ...]
    requirements: tuple[Requirement, ...]
    allowed_scope: tuple[str, ...]
    acceptances: tuple[Acceptance, ...]
    requested_capabilities: tuple[str, ...]
    timeout_seconds: int
    retry_budget: int
    budget: dict[str, int | float]
    integration_mode: str
    revision: int = field(default=1)

    def __post_init__(self) -> None:
        for name, value in (
            ("schema_version", self.schema_version),
            ("harness_version", self.harness_version),
            ("config_version", self.config_version),
        ):
            if not isinstance(value, str) or not value.strip():
                raise EnvelopeInvariantError(f"{name} must not be empty")
        _validate_identifier("task_id", self.task_id)
        _validate_identifier("repository_id", self.repository_id)
        if _FULL_GIT_SHA.fullmatch(self.base_sha) is None:
            raise EnvelopeInvariantError(
                "base_sha must be a full 40- or 64-character lowercase Git SHA"
            )
        if (
            not isinstance(self.worktree_path, Path)
            or not self.worktree_path.is_absolute()
        ):
            raise EnvelopeInvariantError("worktree_path must be absolute")
        if not isinstance(self.initial_dirty_state, tuple) or not all(
            isinstance(item, str) for item in self.initial_dirty_state
        ):
            raise EnvelopeInvariantError(
                "initial_dirty_state must be a tuple of strings"
            )
        if not isinstance(self.goal, str) or not self.goal.strip():
            raise EnvelopeInvariantError("goal must not be empty")
        if not isinstance(self.non_goals, tuple) or not all(
            isinstance(item, str) for item in self.non_goals
        ):
            raise EnvelopeInvariantError("non_goals must be a tuple of strings")
        if not isinstance(self.requirements, tuple) or not self.requirements:
            raise EnvelopeInvariantError("requirements must not be empty")
        if not all(isinstance(item, Requirement) for item in self.requirements):
            raise EnvelopeInvariantError("requirements contains an invalid item")
        if not isinstance(self.allowed_scope, tuple) or not self.allowed_scope:
            raise EnvelopeInvariantError("allowed_scope must not be empty")
        if not all(
            isinstance(item, str) and item for item in self.allowed_scope
        ):
            raise EnvelopeInvariantError(
                "allowed_scope must contain non-empty strings"
            )
        if not isinstance(self.acceptances, tuple) or not self.acceptances:
            raise EnvelopeInvariantError("acceptances must not be empty")
        if not all(isinstance(item, Acceptance) for item in self.acceptances):
            raise EnvelopeInvariantError("acceptances contains an invalid item")
        if not isinstance(self.requested_capabilities, tuple):
            raise EnvelopeInvariantError(
                "requested_capabilities must be a tuple"
            )
        _validate_positive_integer("timeout_seconds", self.timeout_seconds)
        _validate_non_negative_integer("retry_budget", self.retry_budget)
        if not isinstance(self.budget, dict):
            raise EnvelopeInvariantError("budget must be an object")
        if self.integration_mode not in {"observe", "enforced"}:
            raise EnvelopeInvariantError(
                "integration_mode must be 'observe' or 'enforced'"
            )
        _validate_positive_integer("revision", self.revision)
        _reject_duplicates("requirement", (item.id for item in self.requirements))
        _reject_duplicates("acceptance", (item.id for item in self.acceptances))
        _reject_duplicates("allowed scope", self.allowed_scope)
        _reject_duplicates("requested capability", self.requested_capabilities)
        for capability_id in self.requested_capabilities:
            _validate_identifier("requested capability", capability_id)
        requirement_ids = {item.id for item in self.requirements}
        unknown_coverage = {
            covered
            for acceptance in self.acceptances
            for covered in acceptance.covers
            if covered not in requirement_ids
        }
        if unknown_coverage:
            raise EnvelopeInvariantError(
                f"acceptance covers unknown requirements: {sorted(unknown_coverage)}"
            )
        for key, budget_value in self.budget.items():
            if not isinstance(key, str) or not key:
                raise EnvelopeInvariantError("budget keys must be non-empty strings")
            if (
                isinstance(budget_value, bool)
                or not isinstance(budget_value, (int, float))
                or budget_value < 0
            ):
                raise EnvelopeInvariantError(
                    "budget values must be non-negative numbers"
                )
        max_commands = self.budget.get("max_commands")
        if max_commands is not None and (
            type(max_commands) is not int or max_commands < 1
        ):
            raise EnvelopeInvariantError(
                "budget max_commands must be a positive integer"
            )

    def amend(self, **changes: Any) -> Self:
        """Return the next revision after enforcing monotonic task boundaries."""
        forbidden = {"schema_version", "task_id", "repository_id", "revision"}
        invalid_fields = set(changes) - (_ENVELOPE_FIELDS - forbidden)
        if invalid_fields:
            raise EnvelopeInvariantError(
                f"fields cannot be amended: {sorted(invalid_fields)}"
            )
        if "base_sha" in changes:
            candidate_sha = changes["base_sha"]
            if (
                not isinstance(candidate_sha, str)
                or _FULL_GIT_SHA.fullmatch(candidate_sha) is None
            ):
                raise EnvelopeInvariantError(
                    "base_sha must be a full 40- or 64-character lowercase Git SHA"
                )
            if candidate_sha != self.base_sha:
                raise EnvelopeInvariantError("base_sha is immutable within a task")

        normalized = _normalize_amendment(changes)
        immutable_values = {
            "base_sha": self.base_sha,
            "worktree_path": self.worktree_path,
            "initial_dirty_state": self.initial_dirty_state,
            "harness_version": self.harness_version,
            "config_version": self.config_version,
        }
        for name, current_value in immutable_values.items():
            if name in normalized and normalized[name] != current_value:
                raise EnvelopeInvariantError(f"{name} is immutable within a task")
        if "acceptances" in normalized:
            previous_required = {
                acceptance.id for acceptance in self.acceptances if acceptance.required
            }
            candidate_required = {
                acceptance.id
                for acceptance in normalized["acceptances"]
                if acceptance.required
            }
            if not previous_required <= candidate_required:
                raise EnvelopeInvariantError(
                    "required acceptance cannot be removed or made optional"
                )
        if "requested_capabilities" in normalized and not set(
            self.requested_capabilities
        ) <= set(normalized["requested_capabilities"]):
            raise EnvelopeInvariantError(
                "requested capabilities cannot be reduced by an amendment"
            )
        if "allowed_scope" in normalized and not set(self.allowed_scope) <= set(
            normalized["allowed_scope"]
        ):
            raise EnvelopeInvariantError(
                "allowed scope cannot be reduced by an amendment"
            )
        candidate = replace(self, **normalized, revision=self.revision + 1)

        previous_required = {
            acceptance.id for acceptance in self.acceptances if acceptance.required
        }
        candidate_required = {
            acceptance.id for acceptance in candidate.acceptances if acceptance.required
        }
        if not previous_required <= candidate_required:
            raise EnvelopeInvariantError(
                "required acceptance cannot be removed or made optional"
            )
        if not set(self.requested_capabilities) <= set(
            candidate.requested_capabilities
        ):
            raise EnvelopeInvariantError(
                "requested capabilities cannot be reduced by an amendment"
            )
        return candidate

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON-compatible external representation."""
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "repository_id": self.repository_id,
            "base_sha": self.base_sha,
            "worktree_path": str(self.worktree_path),
            "initial_dirty_state": list(self.initial_dirty_state),
            "harness_version": self.harness_version,
            "config_version": self.config_version,
            "goal": self.goal,
            "non_goals": list(self.non_goals),
            "requirements": [item.to_dict() for item in self.requirements],
            "allowed_scope": list(self.allowed_scope),
            "acceptances": [item.to_dict() for item in self.acceptances],
            "requested_capabilities": list(self.requested_capabilities),
            "timeout_seconds": self.timeout_seconds,
            "retry_budget": self.retry_budget,
            "budget": dict(self.budget),
            "integration_mode": self.integration_mode,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        """Load a strict Task Envelope representation."""
        _require_exact_fields(value, _ENVELOPE_FIELDS, "task envelope")
        return cls(
            schema_version=value["schema_version"],
            task_id=value["task_id"],
            repository_id=value["repository_id"],
            base_sha=value["base_sha"],
            worktree_path=Path(value["worktree_path"]),
            initial_dirty_state=tuple(value["initial_dirty_state"]),
            harness_version=value["harness_version"],
            config_version=value["config_version"],
            goal=value["goal"],
            non_goals=tuple(value["non_goals"]),
            requirements=tuple(
                Requirement.from_dict(item) for item in value["requirements"]
            ),
            allowed_scope=tuple(value["allowed_scope"]),
            acceptances=tuple(
                Acceptance.from_dict(item) for item in value["acceptances"]
            ),
            requested_capabilities=tuple(value["requested_capabilities"]),
            timeout_seconds=value["timeout_seconds"],
            retry_budget=value["retry_budget"],
            budget=dict(value["budget"]),
            integration_mode=value["integration_mode"],
            revision=value["revision"],
        )


def _normalize_amendment(changes: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(changes)
    tuple_fields = {
        "initial_dirty_state",
        "non_goals",
        "requirements",
        "allowed_scope",
        "acceptances",
        "requested_capabilities",
    }
    for name in tuple_fields & normalized.keys():
        normalized[name] = tuple(normalized[name])
    if "worktree_path" in normalized:
        normalized["worktree_path"] = Path(normalized["worktree_path"])
    if "budget" in normalized:
        normalized["budget"] = dict(normalized["budget"])
    return normalized


def _validate_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise EnvelopeInvariantError(f"{name} is not a valid identifier")


def _validate_integer(name: str, value: Any) -> None:
    if type(value) is not int:
        raise EnvelopeInvariantError(f"{name} must be an integer")


def _validate_positive_integer(name: str, value: Any) -> None:
    _validate_integer(name, value)
    if value < 1:
        raise EnvelopeInvariantError(f"{name} must be positive")


def _validate_non_negative_integer(name: str, value: Any) -> None:
    _validate_integer(name, value)
    if value < 0:
        raise EnvelopeInvariantError(f"{name} must not be negative")


def _reject_duplicates(name: str, values: Any) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise EnvelopeInvariantError(f"duplicate {name} ids: {sorted(duplicates)}")


def _require_exact_fields(
    value: dict[str, Any], expected: set[str], description: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise EnvelopeInvariantError(
            f"invalid {description} fields; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


__all__ = [
    "Acceptance",
    "EnvelopeInvariantError",
    "Requirement",
    "TaskEnvelope",
]
