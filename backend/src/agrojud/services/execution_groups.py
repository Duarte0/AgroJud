"""Atomic group admission over independent, existing collection jobs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agrojud.config import Settings
from agrojud.db.models import ExecutionGroup, ExecutionGroupMember, Job, JobCheckpoint
from agrojud.domain.canonical_json import sha256_json
from agrojud.services.jobs import EnqueueRequest, EnqueueResult, JobService
from agrojud.sources.contracts import SourceError, SourceErrorCode
from agrojud.sources.tribunal_availability import (
    TribunalAvailabilitySnapshot,
    tribunal_availability,
)
from agrojud.sources.tribunals import canonicalize_tribunal

GroupKind = Literal["discovery", "refresh_number"]
MemberState = Literal[
    "QUEUED",
    "RUNNING",
    "RETRY_WAIT",
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "CANCELLED",
    "BLOCKED_NOT_VALIDATED",
    "NOT_RUN",
]


@dataclass(frozen=True, slots=True)
class CreatedExecutionGroup:
    group_id: UUID
    state: str
    members: tuple[ExecutionGroupMember, ...]
    reused: bool


@dataclass(frozen=True, slots=True)
class ExecutionGroupMemberView:
    id: UUID
    tribunal: str
    state: MemberState
    availability_state: str
    reason: str | None
    job_id: UUID | None
    collection_id: UUID | None
    coverage: dict[str, Any] | None
    next_page: int | None
    checkpoint_revision: int | None
    checkpoint_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionGroupView:
    id: UUID
    kind: GroupKind
    state: str
    matrix_sha256: str | None
    allowlist_version: str | None
    catalog_version: str | None
    tpu_version: str | None
    request_snapshot: dict[str, Any]
    member_count: int
    completed_count: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    members: tuple[ExecutionGroupMemberView, ...]


class ExecutionGroupService:
    """Coordinate durable metadata and independent queue entries in one transaction."""

    def __init__(self, jobs: JobService, settings: Settings) -> None:
        self.jobs = jobs
        self.settings = settings

    def availability(self, *, preset_id: str | None = None) -> TribunalAvailabilitySnapshot:
        return tribunal_availability(self.settings, preset_id=preset_id)

    def create_in_session(
        self,
        session: Session,
        *,
        kind: GroupKind,
        mode: Literal["demo", "real"],
        source: str,
        tribunals: Sequence[str],
        preset_id: str | None,
        request_snapshot: dict[str, Any],
        request_for_tribunal: Callable[[str], EnqueueRequest],
        idempotency_key: str | None = None,
    ) -> CreatedExecutionGroup:
        canonical_tribunals = _canonical_tribunals(tribunals)
        if idempotency_key is not None:
            lock_key = int.from_bytes(bytes.fromhex(idempotency_key[:16]), "big", signed=True)
            session.execute(select(func.pg_advisory_xact_lock(lock_key)))
            existing = session.scalar(
                select(ExecutionGroup)
                .where(
                    ExecutionGroup.idempotency_key == idempotency_key,
                    ExecutionGroup.state.in_(("queued", "running")),
                )
                .with_for_update()
            )
            if existing is not None:
                existing_members = session.scalars(
                    select(ExecutionGroupMember)
                    .where(ExecutionGroupMember.group_id == existing.id)
                    .order_by(ExecutionGroupMember.tribunal.asc())
                ).all()
                return CreatedExecutionGroup(
                    group_id=existing.id,
                    state=existing.state,
                    members=tuple(existing_members),
                    reused=True,
                )

        availability = self.availability(preset_id=preset_id)
        now = datetime.now(UTC)
        group = ExecutionGroup(
            id=uuid4(),
            kind=kind,
            mode=mode,
            source=source,
            state="queued",
            request_snapshot={
                **request_snapshot,
                "tribunals": list(canonical_tribunals),
                "preset_id": preset_id,
            },
            idempotency_key=idempotency_key,
            matrix_sha256=None,
            allowlist_version=availability.allowlist_version,
            catalog_version=None,
            tpu_version=None,
            created_at=now,
            updated_at=now,
        )
        session.add(group)
        session.flush()
        members: list[ExecutionGroupMember] = []
        for tribunal in canonical_tribunals:
            member = ExecutionGroupMember(
                id=uuid4(),
                group_id=group.id,
                tribunal=tribunal,
                state="QUEUED",
                job_id=None,
                reason=None,
                availability_state="NOT_REQUIRED",
                created_at=now,
                updated_at=now,
            )
            session.add(member)
            request = request_for_tribunal(tribunal)
            result: EnqueueResult = self.jobs.enqueue_in_session(session, request)
            job = session.get(Job, result.job_id)
            if job is None:  # pragma: no cover - enqueue flushes the inserted or reused job
                raise RuntimeError("O job filho não pôde ser lido dentro da transação.")
            member.job_id = job.id
            member.state = _job_member_state(job.status)
            members.append(member)
        session.flush()
        group.state = _group_state([item.state for item in members])
        group.finished_at = now if group.state in _TERMINAL_GROUP_STATES else None
        return CreatedExecutionGroup(
            group_id=group.id,
            state=group.state,
            members=tuple(members),
            reused=False,
        )

    @staticmethod
    def inspect_in_session(session: Session, group_id: UUID) -> ExecutionGroupView | None:
        group = session.get(ExecutionGroup, group_id)
        if group is None:
            return None
        members = session.scalars(
            select(ExecutionGroupMember)
            .where(ExecutionGroupMember.group_id == group.id)
            .order_by(ExecutionGroupMember.tribunal.asc())
        ).all()
        job_ids = [member.job_id for member in members if member.job_id is not None]
        jobs = (
            {job.id: job for job in session.scalars(select(Job).where(Job.id.in_(job_ids))).all()}
            if job_ids
            else {}
        )
        checkpoints = (
            {
                checkpoint.job_id: checkpoint
                for checkpoint in session.scalars(
                    select(JobCheckpoint).where(JobCheckpoint.job_id.in_(job_ids))
                ).all()
            }
            if job_ids
            else {}
        )
        member_views: list[ExecutionGroupMemberView] = []
        states: list[str] = []
        for member in members:
            job = jobs.get(member.job_id) if member.job_id is not None else None
            checkpoint = checkpoints.get(member.job_id) if member.job_id is not None else None
            state = _job_member_state(job.status) if job is not None else member.state
            states.append(state)
            coverage = job.coverage if job is not None else None
            member_views.append(
                ExecutionGroupMemberView(
                    id=member.id,
                    tribunal=member.tribunal,
                    state=state,  # type: ignore[arg-type]
                    availability_state=member.availability_state,
                    reason=job.reason if job is not None and job.reason else member.reason,
                    job_id=member.job_id,
                    collection_id=job.collection_id if job is not None else None,
                    coverage=coverage,
                    next_page=checkpoint.next_page if checkpoint is not None else None,
                    checkpoint_revision=checkpoint.revision if checkpoint is not None else None,
                    checkpoint_updated_at=checkpoint.updated_at if checkpoint is not None else None,
                    created_at=member.created_at,
                    updated_at=job.updated_at if job is not None else member.updated_at,
                )
            )
        state = _group_state(states)
        completed = sum(item in {"COMPLETED", "PARTIAL"} for item in states)
        return ExecutionGroupView(
            id=group.id,
            kind=group.kind,  # type: ignore[arg-type]
            state=state,
            matrix_sha256=group.matrix_sha256,
            allowlist_version=group.allowlist_version,
            catalog_version=group.catalog_version,
            tpu_version=group.tpu_version,
            request_snapshot=group.request_snapshot,
            member_count=len(member_views),
            completed_count=completed,
            created_at=group.created_at,
            updated_at=max((item.updated_at for item in member_views), default=group.updated_at),
            finished_at=group.finished_at if state in _TERMINAL_GROUP_STATES else None,
            members=tuple(member_views),
        )

    def cancel_in_session(self, session: Session, group_id: UUID) -> bool:
        group = session.get(ExecutionGroup, group_id)
        if group is None:
            return False
        members = session.scalars(
            select(ExecutionGroupMember)
            .where(ExecutionGroupMember.group_id == group.id)
            .with_for_update()
        ).all()
        for member in members:
            if member.job_id is not None:
                self.jobs.request_cancel(member.job_id)
            elif member.state in {"BLOCKED_NOT_VALIDATED", "NOT_RUN"}:
                member.state = "CANCELLED"
                member.updated_at = datetime.now(UTC)
        return True


_TERMINAL_GROUP_STATES = frozenset({"completed", "partial", "failed", "blocked", "cancelled"})


def _canonical_tribunals(tribunals: Sequence[str]) -> tuple[str, ...]:
    if isinstance(tribunals, (str, bytes)) or not tribunals:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "Selecione ao menos um tribunal.",
            field_path="tribunals",
            validation_code="TRIBUNALS_EMPTY",
        )
    canonical: list[str] = []
    for tribunal in tribunals:
        try:
            resolved = canonicalize_tribunal(tribunal)
        except ValueError, TypeError:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "A seleção contém tribunal fora da allowlist versionada.",
                field_path="tribunals",
                validation_code="TRIBUNAL_NOT_ALLOWLISTED",
            ) from None
        if resolved in canonical:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "A seleção de tribunais não pode conter duplicatas.",
                field_path="tribunals",
                validation_code="TRIBUNAL_DUPLICATE",
            )
        canonical.append(resolved)
    return tuple(canonical)


def _job_member_state(status: str) -> MemberState:
    return {
        "queued": "QUEUED",
        "running": "RUNNING",
        "retry_wait": "RETRY_WAIT",
        "completed": "COMPLETED",
        "partial": "PARTIAL",
        "failed": "FAILED",
        "cancelled": "CANCELLED",
    }.get(status, "NOT_RUN")  # type: ignore[return-value]


def _group_state(states: Sequence[str]) -> str:
    if not states:
        return "blocked"
    if any(state in {"RUNNING", "RETRY_WAIT"} for state in states):
        return "running"
    if any(state == "QUEUED" for state in states):
        return "queued"
    completed = any(state in {"COMPLETED", "PARTIAL"} for state in states)
    has_issue = any(
        state in {"PARTIAL", "FAILED", "BLOCKED_NOT_VALIDATED", "NOT_RUN", "CANCELLED"}
        for state in states
    )
    if completed and has_issue:
        return "partial"
    if any(state == "PARTIAL" for state in states):
        return "partial"
    if all(state in {"BLOCKED_NOT_VALIDATED", "NOT_RUN"} for state in states):
        return "blocked"
    if any(state == "FAILED" for state in states) and not completed:
        return "failed"
    if all(state in {"CANCELLED", "BLOCKED_NOT_VALIDATED", "NOT_RUN"} for state in states):
        return "cancelled" if any(state == "CANCELLED" for state in states) else "blocked"
    if all(state == "COMPLETED" for state in states):
        return "completed"
    if any(state == "COMPLETED" for state in states):
        return "partial"
    return "failed"


def execution_group_idempotency_key(
    *, target_id: UUID, kind: GroupKind, requests: Sequence[EnqueueRequest]
) -> str:
    """Build a stable target key from the independent operation identities in a group."""

    operations = [
        {
            "mode": request.mode,
            "type": request.job_type,
            "source": request.source,
            "tribunal": request.tribunal,
            "query": request.resolved_query,
            "sort": request.sort,
        }
        for request in sorted(requests, key=lambda item: item.tribunal)
    ]
    return sha256_json(
        cast(Any, {"target_id": str(target_id), "kind": kind, "operations": operations})
    )
