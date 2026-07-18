"""Atomic, integrity-protected canonical task record storage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, Self

from adaptive_harness.core.envelope import TaskEnvelope

RECORD_SCHEMA_VERSION = "1.0"
TERMINAL_STATES = {
    "completed",
    "accepted_with_risk",
    "blocked",
    "failed",
    "abandoned",
}
TASK_STATES = {"draft", "active", *TERMINAL_STATES}


class TaskStoreError(RuntimeError):
    """Base class for task store failures."""


class TaskAlreadyExistsError(TaskStoreError):
    """Raised when creating a task whose canonical record already exists."""


class TaskNotFoundError(TaskStoreError):
    """Raised when a task record does not exist."""


class RecordCorruptedError(TaskStoreError):
    """Raised when a task record cannot be trusted."""


class InvalidTaskStateError(TaskStoreError):
    """Raised when an operation is invalid for the current task state."""


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """One immutable fact in a task's append-only history."""

    sequence: int
    type: str
    occurred_at: str
    data: dict[str, Any]

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise RecordCorruptedError("event sequence must be a positive integer")
        if not isinstance(self.type, str) or not self.type:
            raise RecordCorruptedError("event type must not be empty")
        if not isinstance(self.occurred_at, str) or not self.occurred_at:
            raise RecordCorruptedError("event occurred_at must not be empty")
        if not isinstance(self.data, dict):
            raise RecordCorruptedError("event data must be an object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "type": self.type,
            "occurred_at": self.occurred_at,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Self:
        expected = {"sequence", "type", "occurred_at", "data"}
        if set(value) != expected:
            raise RecordCorruptedError("task event has invalid fields")
        return cls(
            sequence=value["sequence"],
            type=value["type"],
            occurred_at=value["occurred_at"],
            data=dict(value["data"]),
        )


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """The canonical record for a governed task."""

    schema_version: str
    task_id: str
    state: str
    envelope_revisions: tuple[TaskEnvelope, ...]
    events: tuple[TaskEvent, ...]

    def __post_init__(self) -> None:
        if self.schema_version != RECORD_SCHEMA_VERSION:
            raise RecordCorruptedError(
                f"unsupported record schema version: {self.schema_version}"
            )
        if self.state not in TASK_STATES:
            raise RecordCorruptedError(f"invalid task state: {self.state}")
        if not self.envelope_revisions:
            raise RecordCorruptedError("record has no envelope revisions")
        if self.task_id != self.envelope_revisions[0].task_id:
            raise RecordCorruptedError("record task_id does not match its envelope")
        revisions = [envelope.revision for envelope in self.envelope_revisions]
        if revisions != list(range(1, len(revisions) + 1)):
            raise RecordCorruptedError("envelope revisions are not contiguous")
        if any(
            envelope.task_id != self.task_id
            for envelope in self.envelope_revisions
        ):
            raise RecordCorruptedError("envelope revision belongs to another task")
        first = self.envelope_revisions[0]
        immutable_fields = (
            "schema_version",
            "repository_id",
            "base_sha",
            "worktree_path",
            "initial_dirty_state",
            "harness_version",
            "config_version",
        )
        for envelope in self.envelope_revisions[1:]:
            for name in immutable_fields:
                if getattr(envelope, name) != getattr(first, name):
                    raise RecordCorruptedError(
                        f"envelope revision changed immutable {name}"
                    )
        for previous, current in pairwise(self.envelope_revisions):
            previous_required = {
                item.id for item in previous.acceptances if item.required
            }
            current_required = {
                item.id for item in current.acceptances if item.required
            }
            if not previous_required <= current_required:
                raise RecordCorruptedError(
                    "envelope revision removed required acceptance"
                )
            if not set(previous.requested_capabilities) <= set(
                current.requested_capabilities
            ):
                raise RecordCorruptedError(
                    "envelope revision reduced requested capabilities"
                )
            if not set(previous.allowed_scope) <= set(current.allowed_scope):
                raise RecordCorruptedError("envelope revision reduced allowed scope")
        sequences = [event.sequence for event in self.events]
        if sequences != list(range(1, len(sequences) + 1)):
            raise RecordCorruptedError("event history is not contiguous")

    @property
    def current_envelope(self) -> TaskEnvelope:
        return self.envelope_revisions[-1]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "state": self.state,
            "envelope_revisions": [
                envelope.to_dict() for envelope in self.envelope_revisions
            ],
            "events": [event.to_dict() for event in self.events],
        }

    @classmethod
    def from_payload(cls, value: dict[str, Any]) -> Self:
        expected = {
            "schema_version",
            "task_id",
            "state",
            "envelope_revisions",
            "events",
        }
        if set(value) != expected:
            raise RecordCorruptedError("task record has invalid fields")
        try:
            return cls(
                schema_version=value["schema_version"],
                task_id=value["task_id"],
                state=value["state"],
                envelope_revisions=tuple(
                    TaskEnvelope.from_dict(item)
                    for item in value["envelope_revisions"]
                ),
                events=tuple(TaskEvent.from_dict(item) for item in value["events"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, RecordCorruptedError):
                raise
            raise RecordCorruptedError("task record payload is invalid") from error


class TaskStore:
    """Persist canonical records with atomic replacement and integrity checks."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._tasks_dir = self._data_dir / "tasks"

    def record_path(self, task_id: str) -> Path:
        if not task_id or any(character not in _SAFE_TASK_ID for character in task_id):
            raise TaskStoreError("task_id is not safe for local storage")
        return self._tasks_dir / task_id / "record.json"

    def create(self, envelope: TaskEnvelope) -> TaskRecord:
        path = self.record_path(envelope.task_id)
        if path.exists():
            raise TaskAlreadyExistsError(f"task already exists: {envelope.task_id}")
        record = TaskRecord(
            schema_version=RECORD_SCHEMA_VERSION,
            task_id=envelope.task_id,
            state="draft",
            envelope_revisions=(envelope,),
            events=(_new_event(1, "task.created", {}),),
        )
        self._write(path, record)
        return record

    def load(self, task_id: str) -> TaskRecord:
        path = self.record_path(task_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise TaskNotFoundError(f"task not found: {task_id}") from error
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RecordCorruptedError("task record is not valid JSON") from error
        if not isinstance(value, dict):
            raise RecordCorruptedError("task record root must be an object")
        integrity = value.pop("integrity", None)
        if not isinstance(integrity, dict):
            raise RecordCorruptedError("task record integrity metadata is missing")
        expected_integrity = _integrity(value)
        if integrity != expected_integrity:
            raise RecordCorruptedError("task record integrity check failed")
        record = TaskRecord.from_payload(value)
        if record.task_id != task_id:
            raise RecordCorruptedError("loaded record belongs to another task")
        return record

    def amend(self, task_id: str, **changes: Any) -> TaskRecord:
        record = self.load(task_id)
        self._require_active(record, "amend")
        envelope = record.current_envelope.amend(**changes)
        amended = TaskRecord(
            schema_version=record.schema_version,
            task_id=record.task_id,
            state=record.state,
            envelope_revisions=(*record.envelope_revisions, envelope),
            events=(
                *record.events,
                _new_event(
                    len(record.events) + 1,
                    "task.amended",
                    {"revision": envelope.revision},
                ),
            ),
        )
        self._write(self.record_path(task_id), amended)
        return amended

    def cancel(self, task_id: str, *, reason: str) -> TaskRecord:
        record = self.load(task_id)
        self._require_active(record, "cancel")
        if not reason.strip():
            raise TaskStoreError("cancellation reason must not be empty")
        cancelled = TaskRecord(
            schema_version=record.schema_version,
            task_id=record.task_id,
            state="abandoned",
            envelope_revisions=record.envelope_revisions,
            events=(
                *record.events,
                _new_event(
                    len(record.events) + 1,
                    "task.cancelled",
                    {"reason": reason},
                ),
            ),
        )
        self._write(self.record_path(task_id), cancelled)
        return cancelled

    def append_event(
        self, task_id: str, event_type: str, data: dict[str, Any]
    ) -> TaskRecord:
        """Atomically append a kernel event to a non-terminal task record."""
        record = self.load(task_id)
        self._require_active(record, "append an event to")
        if not event_type:
            raise TaskStoreError("event_type must not be empty")
        try:
            json.dumps(data, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise TaskStoreError("event data must be JSON-compatible") from error
        updated = TaskRecord(
            schema_version=record.schema_version,
            task_id=record.task_id,
            state=record.state,
            envelope_revisions=record.envelope_revisions,
            events=(
                *record.events,
                _new_event(len(record.events) + 1, event_type, dict(data)),
            ),
        )
        self._write(self.record_path(task_id), updated)
        return updated

    def finalize(
        self,
        task_id: str,
        *,
        outcome: str,
        verification: dict[str, Any],
    ) -> TaskRecord:
        """Persist a Verifier-authorized terminal outcome."""
        if outcome not in {"completed", "accepted_with_risk"}:
            raise InvalidTaskStateError(
                f"Verifier cannot finalize task as {outcome}"
            )
        record = self.load(task_id)
        self._require_active(record, "finalize")
        try:
            json.dumps(verification, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise TaskStoreError(
                "verification data must be JSON-compatible"
            ) from error
        finalized = TaskRecord(
            schema_version=record.schema_version,
            task_id=record.task_id,
            state=outcome,
            envelope_revisions=record.envelope_revisions,
            events=(
                *record.events,
                _new_event(
                    len(record.events) + 1,
                    f"task.{outcome}",
                    dict(verification),
                ),
            ),
        )
        self._write(self.record_path(task_id), finalized)
        return finalized

    def _require_active(self, record: TaskRecord, operation: str) -> None:
        if record.state in TERMINAL_STATES:
            raise InvalidTaskStateError(
                f"cannot {operation} task in terminal state {record.state}"
            )

    def _write(self, path: Path, record: TaskRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = record.to_payload()
        document = {**payload, "integrity": _integrity(payload)}
        serialized = json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".record-",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
            _sync_directory(path.parent)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise


_SAFE_TASK_ID = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _new_event(sequence: int, event_type: str, data: dict[str, Any]) -> TaskEvent:
    return TaskEvent(
        sequence=sequence,
        type=event_type,
        occurred_at=datetime.now(UTC).isoformat(),
        data=data,
    )


def _integrity(payload: dict[str, Any]) -> dict[str, str]:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "algorithm": "sha256",
        "digest": hashlib.sha256(canonical).hexdigest(),
    }


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "InvalidTaskStateError",
    "RecordCorruptedError",
    "TaskAlreadyExistsError",
    "TaskEvent",
    "TaskNotFoundError",
    "TaskRecord",
    "TaskStore",
    "TaskStoreError",
]
