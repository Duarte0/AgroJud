"""Transactional state changes for the one local manual process watchlist."""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agrojud.db.models import Process, ProcessWatchlistEntry, ProcessWatchlistHistory


def set_process_watch(
    session: Session,
    process_id: UUID,
    *,
    active: bool,
) -> tuple[Process | None, ProcessWatchlistEntry | None]:
    """Lock a local process and apply one effective watch transition, if needed."""

    process = session.scalar(select(Process).where(Process.id == process_id).with_for_update())
    if process is None:
        return None, None

    entry = session.scalar(
        select(ProcessWatchlistEntry).where(ProcessWatchlistEntry.process_id == process_id)
    )
    if entry is not None and entry.active is active:
        return process, entry

    now = session.scalar(select(func.now()))
    if now is None:  # pragma: no cover - PostgreSQL always returns transaction time
        raise RuntimeError("O banco não retornou o horário da transição de acompanhamento.")

    if entry is None:
        if not active:
            return process, None
        entry = ProcessWatchlistEntry(
            process_id=process_id,
            active=True,
            included_at=now,
            removed_at=None,
        )
        session.add(entry)
    elif active:
        entry.active = True
        entry.included_at = now
        entry.removed_at = None
    else:
        entry.active = False
        entry.removed_at = now

    session.add(
        ProcessWatchlistHistory(
            id=uuid4(),
            process_id=process_id,
            action="included" if active else "removed",
            created_at=now,
        )
    )
    session.flush()
    return process, entry


def lock_process_watch(
    session: Session,
    *,
    process_id: UUID | None = None,
    process_number: str | None = None,
) -> tuple[Process | None, ProcessWatchlistEntry | None]:
    """Lock a local process and read its watch state in the same transaction."""

    if (process_id is None) == (process_number is None):
        raise ValueError("Informe process_id ou process_number, mas não ambos.")
    predicate = (
        Process.id == process_id if process_id is not None else Process.numero_cnj == process_number
    )
    process = session.scalar(select(Process).where(predicate).with_for_update())
    if process is None:
        return None, None
    entry = session.scalar(
        select(ProcessWatchlistEntry).where(ProcessWatchlistEntry.process_id == process.id)
    )
    return process, entry
