"""Project Profile facts with explicit provenance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ProfileInvariantError(ValueError):
    """Raised when a project fact loses its provenance contract."""


class Provenance(StrEnum):
    OBSERVED = "observed"
    DECLARED = "declared"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProfileFact:
    """One project fact and the evidence supporting its origin."""

    category: str
    value: str | None
    provenance: Provenance
    evidence: str
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.category or not all(
            character.isalnum() or character == "_"
            for character in self.category
        ):
            raise ProfileInvariantError("fact category is invalid")
        if not isinstance(self.provenance, Provenance):
            raise ProfileInvariantError("fact provenance is invalid")
        if not isinstance(self.evidence, str) or not self.evidence:
            raise ProfileInvariantError("fact evidence must not be empty")
        if self.provenance is Provenance.UNKNOWN:
            if self.value is not None:
                raise ProfileInvariantError("unknown fact value must be null")
            if self.confidence is not None:
                raise ProfileInvariantError("unknown fact cannot have confidence")
            return
        if not isinstance(self.value, str) or not self.value:
            raise ProfileInvariantError("known fact value must not be empty")
        if self.provenance is Provenance.INFERRED:
            if (
                self.confidence is None
                or isinstance(self.confidence, bool)
                or not 0 <= self.confidence <= 1
            ):
                raise ProfileInvariantError(
                    "inferred fact confidence must be between 0 and 1"
                )
        elif self.confidence is not None:
            raise ProfileInvariantError(
                "only inferred facts may declare confidence"
            )


@dataclass(frozen=True, slots=True)
class ProjectProfile:
    """Deterministic collection of observed, declared, inferred, and unknown facts."""

    root: Path
    facts: tuple[ProfileFact, ...]

    def __post_init__(self) -> None:
        if not self.root.is_absolute():
            raise ProfileInvariantError("profile root must be absolute")
        if len(set(self.facts)) != len(self.facts):
            raise ProfileInvariantError("profile contains duplicate facts")

    def facts_for(self, category: str) -> tuple[ProfileFact, ...]:
        return tuple(fact for fact in self.facts if fact.category == category)


def observed(category: str, value: str, evidence: str) -> ProfileFact:
    return ProfileFact(
        category=category,
        value=value,
        provenance=Provenance.OBSERVED,
        evidence=evidence,
    )


def build_profile(root: Path, facts: list[ProfileFact]) -> ProjectProfile:
    unique = set(facts)
    ordered = tuple(
        sorted(
            unique,
            key=lambda item: (
                item.category,
                item.value or "",
                item.provenance.value,
                item.evidence,
            ),
        )
    )
    return ProjectProfile(root=root.resolve(), facts=ordered)


__all__ = [
    "ProfileFact",
    "ProfileInvariantError",
    "ProjectProfile",
    "Provenance",
]

