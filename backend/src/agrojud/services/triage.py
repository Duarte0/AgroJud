"""Transactional human triage state and append-only history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agrojud.db.models import Process, ProcessTriage, ProcessTriageHistory

TriageDecision = Literal["pending", "relevant", "discarded"]
RuralLink = Literal["unconfirmed", "confirmed"]


@dataclass(frozen=True)
class TriageState:
    decision: TriageDecision
    rural_link: RuralLink
    note: str
    version: int
    updated_at: datetime | None


class ProcessTriageNotFoundError(Exception):
    """Raised when the process does not exist."""


class TriageVersionConflictError(Exception):
    """Raised when a client edits a stale triage version."""


class TriageNoteRequiredError(Exception):
    """Raised when a confirmed rural link has no non-blank justification."""


def default_triage_state() -> TriageState:
    """Return the unpersisted initial state used for reads and version checks."""

    return TriageState(
        decision="pending",
        rural_link="unconfirmed",
        note="",
        version=0,
        updated_at=None,
    )


def state_from_record(record: ProcessTriage | None) -> TriageState:
    if record is None:
        return default_triage_state()
    return TriageState(
        decision=record.decision,  # type: ignore[arg-type]
        rural_link=record.rural_link,  # type: ignore[arg-type]
        note=record.note,
        version=record.version,
        updated_at=record.updated_at,
    )


def triage_state_for_process(session: Session, process_id: UUID) -> TriageState:
    if session.get(Process, process_id) is None:
        raise ProcessTriageNotFoundError
    return state_from_record(session.get(ProcessTriage, process_id))


def update_triage(
    session: Session,
    process_id: UUID,
    *,
    expected_version: int,
    changes: dict[str, str],
) -> TriageState:
    """Compare, update, and append history inside one database transaction."""

    with session.begin():
        process = session.scalar(select(Process).where(Process.id == process_id).with_for_update())
        if process is None:
            raise ProcessTriageNotFoundError

        record = session.scalar(
            select(ProcessTriage).where(ProcessTriage.process_id == process_id).with_for_update()
        )
        current = state_from_record(record)
        if current.version != expected_version:
            raise TriageVersionConflictError

        decision = changes.get("decision", current.decision)
        rural_link = changes.get("rural_link", current.rural_link)
        note = changes.get("note", current.note)
        if "note" in changes:
            note = note.strip()
        if rural_link == "confirmed" and not note.strip():
            raise TriageNoteRequiredError

        if (
            decision == current.decision
            and rural_link == current.rural_link
            and note == current.note
        ):
            return current

        now = session.scalar(select(func.now()))
        if now is None:
            raise RuntimeError("O banco não retornou o horário da atualização da triagem.")
        next_version = current.version + 1
        updated = TriageState(
            decision=decision,  # type: ignore[arg-type]
            rural_link=rural_link,  # type: ignore[arg-type]
            note=note,
            version=next_version,
            updated_at=now,
        )

        if record is None:
            record = ProcessTriage(
                process_id=process_id,
                decision=updated.decision,
                rural_link=updated.rural_link,
                note=updated.note,
                version=updated.version,
                updated_at=now,
            )
            session.add(record)
        else:
            record.decision = updated.decision
            record.rural_link = updated.rural_link
            record.note = updated.note
            record.version = updated.version
            record.updated_at = now
        session.flush()

        session.add(
            ProcessTriageHistory(
                process_id=process_id,
                version=updated.version,
                previous_state=_snapshot(current),
                new_state=_snapshot(updated),
                origin="manual",
            )
        )
        session.flush()
        return updated


def _snapshot(state: TriageState) -> dict[str, str | int]:
    return {
        "decision": state.decision,
        "rural_link": state.rural_link,
        "note": state.note,
        "version": state.version,
    }


def require_process(session: Session, process_id: UUID) -> None:
    if session.get(Process, process_id) is None:
        raise ProcessTriageNotFoundError


def triage_history(
    session: Session,
    process_id: UUID,
    *,
    page: int,
    page_size: int,
) -> tuple[list[ProcessTriageHistory], int]:
    require_process(session, process_id)
    total = (
        session.scalar(
            select(func.count())
            .select_from(ProcessTriageHistory)
            .where(ProcessTriageHistory.process_id == process_id)
        )
        or 0
    )
    rows = session.scalars(
        select(ProcessTriageHistory)
        .where(ProcessTriageHistory.process_id == process_id)
        .order_by(ProcessTriageHistory.version.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return list(rows), total
