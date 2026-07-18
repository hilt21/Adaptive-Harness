"""Requirement-level evidence verification and completion gating."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from adaptive_harness.core.envelope import Acceptance, TaskEnvelope
from adaptive_harness.core.executor import LeaseConflictError, WorkspaceLease
from adaptive_harness.core.gateway import WorkspaceSnapshot
from adaptive_harness.core.store import TaskEvent, TaskRecord, TaskStore


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    """One concrete reason a task cannot be marked completed."""

    code: str
    message: str
    waivable: bool


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    """Evidence outcome for one declared acceptance."""

    acceptance_id: str
    status: str
    evidence_event_sequence: int | None


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """The auditable result of one Completion Gate evaluation."""

    task_id: str
    envelope_revision: int
    outcome: str
    acceptances: tuple[AcceptanceResult, ...]
    issues: tuple[VerificationIssue, ...]
    risk_reason: str | None = None


class Verifier:
    """Verify current external facts before changing canonical task state."""

    def __init__(
        self,
        *,
        store: TaskStore,
        artifact_root: Path,
        workspace_probe: Callable[[], WorkspaceSnapshot],
        diff_probe: Callable[[TaskEnvelope], tuple[str, ...]],
        secrets: tuple[str, ...] = (),
    ) -> None:
        if any(not isinstance(secret, str) or not secret for secret in secrets):
            raise ValueError("secrets must contain non-empty strings")
        self._store = store
        self._artifact_root = Path(artifact_root)
        self._workspace_probe = workspace_probe
        self._diff_probe = diff_probe
        self._secrets = tuple(sorted(set(secrets), key=len, reverse=True))

    def verify(
        self,
        task_id: str,
        *,
        accept_risk: bool = False,
        risk_reason: str | None = None,
    ) -> VerificationReport:
        """Evaluate and, only when legal, persist a terminal task outcome."""
        record = self._store.load(task_id)
        envelope = record.current_envelope
        try:
            with WorkspaceLease(
                self._artifact_root / "leases",
                envelope.worktree_path,
                task_id,
            ):
                return self._verify_with_lease(
                    task_id,
                    accept_risk=accept_risk,
                    risk_reason=risk_reason,
                )
        except LeaseConflictError:
            issue = VerificationIssue(
                code="lease_conflict",
                message="workspace lease is held by another operation",
                waivable=False,
            )
            return VerificationReport(
                task_id=task_id,
                envelope_revision=envelope.revision,
                outcome="rejected",
                acceptances=(),
                issues=(issue,),
                risk_reason=risk_reason,
            )

    def _verify_with_lease(
        self,
        task_id: str,
        *,
        accept_risk: bool,
        risk_reason: str | None,
    ) -> VerificationReport:
        record = self._store.load(task_id)
        envelope = record.current_envelope
        issues: list[VerificationIssue] = []
        self._check_workspace(envelope, issues)
        self._check_diff(envelope, issues)
        self._check_incomplete_commands(record, issues)
        self._check_high_risk_approvals(record, issues)

        acceptance_results = tuple(
            self._verify_acceptance(record, acceptance, issues)
            for acceptance in envelope.acceptances
        )
        self._check_requirement_coverage(
            envelope, acceptance_results, issues
        )

        issues_tuple = _deduplicate_issues(issues)
        outcome = "rejected"
        if not issues_tuple:
            outcome = "completed"
        elif (
            accept_risk
            and risk_reason is not None
            and risk_reason.strip()
            and all(issue.waivable for issue in issues_tuple)
        ):
            outcome = "accepted_with_risk"

        report = VerificationReport(
            task_id=task_id,
            envelope_revision=envelope.revision,
            outcome=outcome,
            acceptances=acceptance_results,
            issues=issues_tuple,
            risk_reason=risk_reason,
        )
        if outcome in {"completed", "accepted_with_risk"}:
            self._store.finalize(
                task_id,
                outcome=outcome,
                verification={
                    "envelope_revision": envelope.revision,
                    "acceptances": [
                        {
                            "id": item.acceptance_id,
                            "status": item.status,
                            "evidence_event_sequence": (
                                item.evidence_event_sequence
                            ),
                        }
                        for item in acceptance_results
                    ],
                    "issues": [item.code for item in issues_tuple],
                    "risk_reason": risk_reason,
                },
            )
        return report

    def _check_workspace(
        self,
        envelope: TaskEnvelope,
        issues: list[VerificationIssue],
    ) -> None:
        snapshot = self._workspace_probe()
        if (
            snapshot.repository_id != envelope.repository_id
            or snapshot.base_sha != envelope.base_sha
            or snapshot.worktree_path != envelope.worktree_path
        ):
            issues.append(
                VerificationIssue(
                    code="workspace_mismatch",
                    message="repository, base SHA, or worktree identity changed",
                    waivable=False,
                )
            )

    def _check_diff(
        self,
        envelope: TaskEnvelope,
        issues: list[VerificationIssue],
    ) -> None:
        allowed = tuple(PurePosixPath(path) for path in envelope.allowed_scope)
        for changed_path in self._diff_probe(envelope):
            candidate = PurePosixPath(changed_path)
            safe_relative = (
                not candidate.is_absolute() and ".." not in candidate.parts
            )
            in_scope = safe_relative and any(
                candidate == scope or candidate.is_relative_to(scope)
                for scope in allowed
            )
            if not in_scope:
                issues.append(
                    VerificationIssue(
                        code="diff_out_of_scope",
                        message=(
                            "changed path is outside allowed scope: "
                            f"{changed_path}"
                        ),
                        waivable=False,
                    )
                )

    def _check_incomplete_commands(
        self, record: TaskRecord, issues: list[VerificationIssue]
    ) -> None:
        finished = {
            event.data.get("authorization_event_sequence")
            for event in record.events
            if event.type == "command.finished"
        }
        for event in record.events:
            if (
                event.type == "command.started"
                and event.data.get("authorization_event_sequence") not in finished
            ):
                issues.append(
                    VerificationIssue(
                        code="command_incomplete",
                        message="a command started without a terminal event",
                        waivable=True,
                    )
                )

    def _check_high_risk_approvals(
        self, record: TaskRecord, issues: list[VerificationIssue]
    ) -> None:
        high_risk = {"production", "credentials", "irreversible"}
        for event in record.events:
            if event.type != "command.authorized":
                continue
            side_effects = set(event.data.get("side_effects", []))
            is_high_risk = (
                bool(side_effects & high_risk)
                or event.data.get("environment") == "production"
            )
            if is_high_risk and event.data.get("approval_id") is None:
                issues.append(
                    VerificationIssue(
                        code="unapproved_high_risk_operation",
                        message="high-risk operation lacks a scoped approval",
                        waivable=False,
                    )
                )

    def _verify_acceptance(
        self,
        record: TaskRecord,
        acceptance: Acceptance,
        issues: list[VerificationIssue],
    ) -> AcceptanceResult:
        if acceptance.evidence_type != "command_event":
            issues.append(
                VerificationIssue(
                    code="unsupported_evidence_type",
                    message=(
                        f"acceptance {acceptance.id} uses unsupported evidence "
                        f"type {acceptance.evidence_type}"
                    ),
                    waivable=False,
                )
            )
            return AcceptanceResult(acceptance.id, "untrusted", None)

        candidates = [
            event
            for event in record.events
            if event.type == "command.finished"
            and event.data.get("capability_id") == acceptance.command_id
        ]
        if not candidates:
            return self._failed_acceptance(
                acceptance, "missing", None, issues
            )
        event = candidates[-1]
        authorization = _authorization_event(record, event)
        if authorization is None:
            issues.append(
                VerificationIssue(
                    code="untrusted_evidence",
                    message=f"acceptance {acceptance.id} has no valid authorization",
                    waivable=False,
                )
            )
            return AcceptanceResult(acceptance.id, "untrusted", event.sequence)
        if (
            authorization.data.get("envelope_revision")
            != record.current_envelope.revision
        ):
            return self._failed_acceptance(
                acceptance, "stale", event.sequence, issues
            )
        if not _has_started_event(record, event):
            issues.append(
                VerificationIssue(
                    code="untrusted_evidence",
                    message=f"acceptance {acceptance.id} has no matching start event",
                    waivable=False,
                )
            )
            return AcceptanceResult(acceptance.id, "untrusted", event.sequence)
        if event.data.get("producer") != "adaptive_harness.executor":
            issues.append(
                VerificationIssue(
                    code="untrusted_evidence",
                    message=f"acceptance {acceptance.id} was not produced by Executor",
                    waivable=False,
                )
            )
            return AcceptanceResult(acceptance.id, "untrusted", event.sequence)

        artifacts_valid = self._check_artifacts(
            record.task_id, acceptance, event, issues
        )
        if (
            record.current_envelope.integration_mode == "enforced"
            and event.data.get("sandbox_enforced") is not True
        ):
            issues.append(
                VerificationIssue(
                    code="sandbox_not_enforced",
                    message="enforced task evidence lacks an enforced sandbox",
                    waivable=False,
                )
            )
            artifacts_valid = False

        passed = (
            artifacts_valid
            and event.data.get("status") == "succeeded"
            and event.data.get("exit_code") == acceptance.expected_exit_code
            and event.data.get("timed_out") is False
            and event.data.get("cancelled") is False
        )
        if passed:
            return AcceptanceResult(acceptance.id, "passed", event.sequence)
        return self._failed_acceptance(
            acceptance, "failed", event.sequence, issues
        )

    def _failed_acceptance(
        self,
        acceptance: Acceptance,
        status: str,
        event_sequence: int | None,
        issues: list[VerificationIssue],
    ) -> AcceptanceResult:
        if acceptance.required:
            issues.append(
                VerificationIssue(
                    code="required_acceptance_failed",
                    message=(
                        f"required acceptance {acceptance.id} is {status}"
                    ),
                    waivable=True,
                )
            )
        return AcceptanceResult(acceptance.id, status, event_sequence)

    def _check_artifacts(
        self,
        task_id: str,
        acceptance: Acceptance,
        event: TaskEvent,
        issues: list[VerificationIssue],
    ) -> bool:
        valid = True
        for field in ("stdout_artifact", "stderr_artifact"):
            raw_path = event.data.get(field)
            if not isinstance(raw_path, str):
                issues.append(
                    VerificationIssue(
                        code="evidence_missing",
                        message=f"acceptance {acceptance.id} lacks {field}",
                        waivable=True,
                    )
                )
                valid = False
                continue
            path = Path(raw_path)
            expected_root = (self._artifact_root / task_id).resolve()
            try:
                resolved = path.resolve(strict=True)
            except FileNotFoundError:
                issues.append(
                    VerificationIssue(
                        code="evidence_missing",
                        message=f"acceptance artifact does not exist: {path}",
                        waivable=True,
                    )
                )
                valid = False
                continue
            if (
                not path.is_absolute()
                or path.is_symlink()
                or not resolved.is_relative_to(expected_root)
                or not resolved.is_file()
            ):
                issues.append(
                    VerificationIssue(
                        code="artifact_escape",
                        message=f"acceptance artifact escapes task storage: {path}",
                        waivable=False,
                    )
                )
                valid = False
                continue
            content = resolved.read_text(encoding="utf-8", errors="replace")
            if any(secret in content for secret in self._secrets):
                issues.append(
                    VerificationIssue(
                        code="secret_leak",
                        message=f"secret found in acceptance artifact: {path}",
                        waivable=False,
                    )
                )
                valid = False
        return valid

    def _check_requirement_coverage(
        self,
        envelope: TaskEnvelope,
        results: tuple[AcceptanceResult, ...],
        issues: list[VerificationIssue],
    ) -> None:
        result_by_id = {item.acceptance_id: item for item in results}
        for requirement in envelope.requirements:
            covering = [
                acceptance
                for acceptance in envelope.acceptances
                if requirement.id in acceptance.covers
            ]
            if not covering:
                issues.append(
                    VerificationIssue(
                        code="requirement_uncovered",
                        message=f"requirement has no acceptance: {requirement.id}",
                        waivable=False,
                    )
                )
                continue
            if not any(
                result_by_id[item.id].status == "passed" for item in covering
            ):
                issues.append(
                    VerificationIssue(
                        code="requirement_unverified",
                        message=(
                            f"requirement lacks passing evidence: {requirement.id}"
                        ),
                        waivable=True,
                    )
                )


def _authorization_event(
    record: TaskRecord, finished: TaskEvent
) -> TaskEvent | None:
    sequence = finished.data.get("authorization_event_sequence")
    if type(sequence) is not int or sequence < 1 or sequence > len(record.events):
        return None
    event = record.events[sequence - 1]
    if (
        event.type != "command.authorized"
        or event.data.get("capability_id")
        != finished.data.get("capability_id")
    ):
        return None
    return event


def _has_started_event(record: TaskRecord, finished: TaskEvent) -> bool:
    authorization_sequence = finished.data.get("authorization_event_sequence")
    return any(
        event.sequence < finished.sequence
        and event.type == "command.started"
        and event.data.get("authorization_event_sequence")
        == authorization_sequence
        for event in record.events
    )


def _deduplicate_issues(
    issues: list[VerificationIssue],
) -> tuple[VerificationIssue, ...]:
    unique: list[VerificationIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.code, issue.message)
        if key not in seen:
            unique.append(issue)
            seen.add(key)
    return tuple(unique)


__all__ = [
    "AcceptanceResult",
    "VerificationIssue",
    "VerificationReport",
    "Verifier",
]
