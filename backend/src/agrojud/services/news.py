"""Watch-cycle baselines and idempotent historical observations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from agrojud.db.models import (
    MovementOccurrence,
    MovementSnapshot,
    MovementSnapshotOccurrence,
    Process,
    ProcessNews,
    ProcessWatchCycle,
    ProcessWatchlistEntry,
    Representation,
    RepresentationVersion,
    RepresentationWatchBaseline,
)

NewsProvenance = Literal["ingestion", "quarantine_reprocess"]


def start_watch_cycle(session: Session, *, process_id: UUID, started_at: datetime) -> None:
    """Start a new watch cycle and capture each representation's local baseline."""

    previous_number = session.scalar(
        select(func.max(ProcessWatchCycle.cycle_number)).where(
            ProcessWatchCycle.process_id == process_id
        )
    )
    cycle = ProcessWatchCycle(
        id=uuid4(),
        process_id=process_id,
        cycle_number=(previous_number or 0) + 1,
        started_at=started_at,
    )
    session.add(cycle)
    session.flush()

    representations = session.scalars(
        select(Representation)
        .where(Representation.process_id == process_id)
        .order_by(Representation.id.asc())
    ).all()
    for representation in representations:
        snapshot = _latest_snapshot_for_projection(session, representation)
        if snapshot is not None and snapshot.is_complete:
            session.add(
                RepresentationWatchBaseline(
                    id=uuid4(),
                    watch_cycle_id=cycle.id,
                    process_id=process_id,
                    representation_id=representation.id,
                    state="established",
                    version_id=snapshot.version_id,
                    snapshot_id=snapshot.id,
                    normalizer_version=snapshot.normalizer_version,
                    established_at=started_at,
                )
            )
        else:
            session.add(
                RepresentationWatchBaseline(
                    id=uuid4(),
                    watch_cycle_id=cycle.id,
                    process_id=process_id,
                    representation_id=representation.id,
                    state="pending",
                )
            )
    session.flush()


def end_watch_cycle(session: Session, *, process_id: UUID, ended_at: datetime) -> None:
    """Stop generation in the active cycle while retaining all its evidence."""

    cycle = session.scalar(
        select(ProcessWatchCycle)
        .where(
            ProcessWatchCycle.process_id == process_id,
            ProcessWatchCycle.ended_at.is_(None),
        )
        .with_for_update()
    )
    if cycle is not None:
        cycle.ended_at = ended_at
        session.flush()


def watch_baselines(
    session: Session, *, process_id: UUID
) -> Sequence[tuple[RepresentationWatchBaseline, Representation]]:
    """Return baselines for the active cycle; inactive cycles remain persisted."""

    cycle_id = session.scalar(
        select(ProcessWatchCycle.id).where(
            ProcessWatchCycle.process_id == process_id,
            ProcessWatchCycle.ended_at.is_(None),
        )
    )
    if cycle_id is None:
        return ()
    return tuple(
        session.execute(
            select(RepresentationWatchBaseline, Representation)
            .join(
                Representation,
                Representation.id == RepresentationWatchBaseline.representation_id,
            )
            .where(RepresentationWatchBaseline.watch_cycle_id == cycle_id)
            .order_by(RepresentationWatchBaseline.representation_id.asc())
        ).all()
    )


def register_watched_snapshot(
    session: Session,
    *,
    process_id: UUID,
    representation: Representation,
    version: RepresentationVersion,
    snapshot: MovementSnapshot,
    provenance: NewsProvenance,
    first_observed_at: datetime,
) -> None:
    """Apply a normalized snapshot to the active baseline and news feed.

    The caller owns the surrounding page or quarantine transaction. Partial
    snapshots can add technical history, but publish no movement differences.
    A later complete snapshot re-evaluates every occurrence first observed after
    the established baseline.
    """

    if not session.in_transaction():
        raise RuntimeError("A comparação de novidades exige a transação do chamador.")
    if representation.process_id != process_id or snapshot.representation_id != representation.id:
        raise ValueError("A representação e o snapshot precisam pertencer ao processo informado.")
    if version.representation_id != representation.id or snapshot.version_id != version.id:
        raise ValueError("A versão e o snapshot precisam pertencer à representação informada.")
    if first_observed_at.tzinfo is None or first_observed_at.utcoffset() is None:
        raise ValueError("A primeira observação da novidade precisa conter fuso.")

    # Watch transitions take the same process lock, so either the page precedes
    # inclusion/removal or it observes the committed active cycle.
    locked_process_id = session.scalar(
        select(Process.id).where(Process.id == process_id).with_for_update()
    )
    if locked_process_id is None:
        raise ValueError("O processo da representação não existe.")
    entry = session.get(ProcessWatchlistEntry, process_id)
    if entry is None or not entry.active:
        return
    cycle = session.scalar(
        select(ProcessWatchCycle)
        .where(
            ProcessWatchCycle.process_id == process_id,
            ProcessWatchCycle.ended_at.is_(None),
        )
        .with_for_update()
    )
    if cycle is None:
        raise RuntimeError("O acompanhamento ativo não possui ciclo histórico.")

    baseline = session.scalar(
        select(RepresentationWatchBaseline)
        .where(
            RepresentationWatchBaseline.watch_cycle_id == cycle.id,
            RepresentationWatchBaseline.representation_id == representation.id,
        )
        .with_for_update()
    )
    if baseline is None:
        other_baseline_exists = (
            session.scalar(
                select(RepresentationWatchBaseline.id)
                .where(
                    RepresentationWatchBaseline.watch_cycle_id == cycle.id,
                    RepresentationWatchBaseline.representation_id != representation.id,
                    RepresentationWatchBaseline.state == "established",
                )
                .limit(1)
            )
            is not None
        )
        baseline = RepresentationWatchBaseline(
            id=uuid4(),
            watch_cycle_id=cycle.id,
            process_id=process_id,
            representation_id=representation.id,
            state="pending",
        )
        session.add(baseline)
        session.flush()
        if other_baseline_exists:
            _insert_news(
                session,
                process_id=process_id,
                representation=representation,
                cycle=cycle,
                category="NEW_REPRESENTATION",
                identity_key=f"representation:{representation.id}",
                occurrence=None,
                version=version,
                snapshot=snapshot,
                source_date=None,
                source_date_original=None,
                source_date_status="unknown",
                first_observed_at=first_observed_at,
                evidence={
                    "representation_id": str(representation.id),
                    "source": representation.source,
                    "tribunal": representation.tribunal,
                    "source_id": representation.source_id,
                    "first_version_id": str(version.id),
                    "snapshot_id": str(snapshot.id),
                },
                provenance=provenance,
            )
        if snapshot.is_complete:
            _establish_baseline(baseline, snapshot=snapshot, established_at=snapshot.processed_at)
        return

    if baseline.state == "pending":
        if snapshot.is_complete:
            _establish_baseline(baseline, snapshot=snapshot, established_at=snapshot.processed_at)
        return
    if not snapshot.is_complete or baseline.normalizer_version != snapshot.normalizer_version:
        return

    present = session.execute(
        select(MovementSnapshotOccurrence, MovementOccurrence)
        .join(
            MovementOccurrence,
            MovementOccurrence.id == MovementSnapshotOccurrence.occurrence_id,
        )
        .where(
            MovementSnapshotOccurrence.snapshot_id == snapshot.id,
            MovementSnapshotOccurrence.representation_id == representation.id,
            MovementSnapshotOccurrence.present.is_(True),
        )
        .order_by(MovementOccurrence.content_sha256, MovementOccurrence.multiplicity_ordinal)
    ).all()
    for association, occurrence in present:
        if (
            baseline.established_at is None
            or occurrence.first_observed_at <= baseline.established_at
        ):
            continue
        alteration_detail = _alteration_evidence(
            session,
            association=association,
            occurrence=occurrence,
        )
        category: Literal["NEW_OBSERVATION", "ALTERATION_OBSERVED"] = (
            "ALTERATION_OBSERVED" if alteration_detail is not None else "NEW_OBSERVATION"
        )
        identity_key = (
            f"{occurrence.normalizer_version}:{occurrence.content_sha256}:"
            f"{occurrence.multiplicity_ordinal}"
        )
        evidence: dict[str, Any] = {
            "occurrence_id": str(occurrence.id),
            "identity": {
                "normalizer_version": occurrence.normalizer_version,
                "content_sha256": occurrence.content_sha256,
                "multiplicity_ordinal": occurrence.multiplicity_ordinal,
            },
            "content": occurrence.normalized_content,
            "baseline_snapshot_id": str(baseline.snapshot_id),
            "published_from_snapshot_id": str(snapshot.id),
        }
        if alteration_detail is not None:
            evidence["alteration"] = alteration_detail
        _insert_news(
            session,
            process_id=process_id,
            representation=representation,
            cycle=cycle,
            category=category,
            identity_key=identity_key,
            occurrence=occurrence,
            version=version,
            snapshot=snapshot,
            source_date=occurrence.source_date_normalized,
            source_date_original=occurrence.source_date_original,
            source_date_status=occurrence.source_date_status,
            first_observed_at=occurrence.first_observed_at,
            evidence=evidence,
            provenance=provenance,
        )


def _latest_snapshot_for_projection(
    session: Session, representation: Representation
) -> MovementSnapshot | None:
    if representation.latest_version_id is None:
        return None
    return session.scalar(
        select(MovementSnapshot)
        .where(MovementSnapshot.version_id == representation.latest_version_id)
        .order_by(MovementSnapshot.processed_at.desc(), MovementSnapshot.id.desc())
        .limit(1)
    )


def _establish_baseline(
    baseline: RepresentationWatchBaseline,
    *,
    snapshot: MovementSnapshot,
    established_at: datetime,
) -> None:
    if not snapshot.is_complete:
        raise ValueError("Somente snapshots normalizados completos estabelecem uma baseline.")
    baseline.state = "established"
    baseline.version_id = snapshot.version_id
    baseline.snapshot_id = snapshot.id
    baseline.normalizer_version = snapshot.normalizer_version
    baseline.established_at = established_at


def _alteration_evidence(
    session: Session,
    *,
    association: MovementSnapshotOccurrence,
    occurrence: MovementOccurrence,
) -> dict[str, Any] | None:
    detail = association.comparison_detail
    if association.comparison_result != "ALTERATION_OBSERVED":
        earlier = session.execute(
            select(MovementSnapshotOccurrence.comparison_detail)
            .join(
                MovementSnapshot,
                MovementSnapshot.id == MovementSnapshotOccurrence.snapshot_id,
            )
            .where(
                MovementSnapshotOccurrence.occurrence_id == occurrence.id,
                MovementSnapshotOccurrence.comparison_result == "ALTERATION_OBSERVED",
                MovementSnapshotOccurrence.comparison_detail.is_not(None),
            )
            .order_by(MovementSnapshot.processed_at.asc(), MovementSnapshot.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        detail = earlier
    if not isinstance(detail, dict):
        return None

    previous_values: list[dict[str, Any]] = []
    previous_identities = detail.get("previous")
    if isinstance(previous_identities, list):
        for identity in previous_identities:
            if not isinstance(identity, dict):
                continue
            content_sha256 = identity.get("content_sha256")
            ordinal = identity.get("ordinal")
            if (
                not isinstance(content_sha256, str)
                or isinstance(ordinal, bool)
                or not isinstance(ordinal, int)
            ):
                continue
            previous = session.scalar(
                select(MovementOccurrence).where(
                    MovementOccurrence.representation_id == occurrence.representation_id,
                    MovementOccurrence.normalizer_version == occurrence.normalizer_version,
                    MovementOccurrence.content_sha256 == content_sha256,
                    MovementOccurrence.multiplicity_ordinal == ordinal,
                )
            )
            if previous is not None:
                previous_values.append(
                    {
                        "identity": identity,
                        "occurrence_id": str(previous.id),
                        "content": previous.normalized_content,
                    }
                )
    return {"comparison": detail, "previous_occurrences": previous_values}


def _insert_news(
    session: Session,
    *,
    process_id: UUID,
    representation: Representation,
    cycle: ProcessWatchCycle,
    category: Literal["NEW_OBSERVATION", "ALTERATION_OBSERVED", "NEW_REPRESENTATION"],
    identity_key: str,
    occurrence: MovementOccurrence | None,
    version: RepresentationVersion,
    snapshot: MovementSnapshot,
    source_date: datetime | None,
    source_date_original: Any,
    source_date_status: str,
    first_observed_at: datetime,
    evidence: dict[str, Any],
    provenance: NewsProvenance,
) -> None:
    session.execute(
        pg_insert(ProcessNews)
        .values(
            id=uuid4(),
            process_id=process_id,
            representation_id=representation.id,
            watch_cycle_id=cycle.id,
            category=category,
            status="pending",
            identity_key=identity_key,
            occurrence_id=occurrence.id if occurrence is not None else None,
            origin_version_id=version.id,
            origin_snapshot_id=snapshot.id,
            source_date=source_date,
            source_date_original=source_date_original,
            source_date_status=source_date_status,
            first_observed_at=first_observed_at,
            evidence=evidence,
            provenance=provenance,
        )
        .on_conflict_do_nothing(
            index_elements=[
                ProcessNews.process_id,
                ProcessNews.representation_id,
                ProcessNews.identity_key,
                ProcessNews.category,
            ]
        )
    )
