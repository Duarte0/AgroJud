"""Read-only process, representation, and movement queries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.process_filters import (
    ProcessFilters,
    SignalEnvironment,
    process_filters_dependency,
    process_predicates,
)
from agrojud.api.schemas import (
    JobStatus,
    LatestCollectionResponse,
    MovementDiagnosticResponse,
    PaginationResponse,
    ProcessDetailResponse,
    ProcessMovementResponse,
    ProcessMovementsResponse,
    ProcessRepresentationResponse,
    ProcessSummaryResponse,
    ProcessTriageStateResponse,
)
from agrojud.db.models import (
    Collection,
    CollectionResult,
    Job,
    MovementOccurrence,
    MovementSnapshot,
    MovementSnapshotOccurrence,
    Process,
    ProcessTriage,
    Representation,
)
from agrojud.services.triage import state_from_record

router = APIRouter(prefix="/api/v1/processes", tags=["processes"])


@router.get(
    "",
    response_model=PaginationResponse[ProcessSummaryResponse],
    responses=ERROR_RESPONSES,
    summary="Pesquisa processos persistidos",
)
def list_processes(
    request: Request,
    filters: Annotated[ProcessFilters, Depends(process_filters_dependency)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> PaginationResponse[ProcessSummaryResponse]:
    environment: SignalEnvironment = (
        "real" if request.app.state.settings.environment == "real" else "demo"
    )
    predicates = process_predicates(filters, environment=environment)
    with request.app.state.session_factory() as session:
        total = session.scalar(select(func.count()).select_from(Process).where(*predicates)) or 0
        processes = session.scalars(
            select(Process)
            .where(*predicates)
            .order_by(Process.numero_cnj.asc(), Process.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = _load_process_summaries(session, processes)
    return PaginationResponse(items=items, page=page, page_size=page_size, total=total)


@router.get(
    "/{process_id}",
    response_model=ProcessDetailResponse,
    responses=ERROR_RESPONSES,
    summary="Consulta um processo local",
)
def get_process(process_id: UUID, request: Request) -> ProcessDetailResponse:
    with request.app.state.session_factory() as session:
        process = session.get(Process, process_id)
        if process is None:
            raise HTTPException(status_code=404)
        summary = _load_process_summaries(session, [process])[0]
    return ProcessDetailResponse.model_validate(summary.model_dump())


@router.get(
    "/{process_id}/representations",
    response_model=PaginationResponse[ProcessRepresentationResponse],
    responses=ERROR_RESPONSES,
    summary="Lista representações e seus diagnósticos",
)
def list_representations(
    process_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> PaginationResponse[ProcessRepresentationResponse]:
    with request.app.state.session_factory() as session:
        _require_process(session, process_id)
        total = (
            session.scalar(
                select(func.count())
                .select_from(Representation)
                .where(Representation.process_id == process_id)
            )
            or 0
        )
        representations = session.scalars(
            select(Representation)
            .where(Representation.process_id == process_id)
            .order_by(
                Representation.source.asc(), Representation.tribunal.asc(), Representation.id.asc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = _load_representation_views(session, representations)
    return PaginationResponse(items=items, page=page, page_size=page_size, total=total)


@router.get(
    "/{process_id}/movements",
    response_model=ProcessMovementsResponse,
    responses=ERROR_RESPONSES,
    summary="Lista movimentos normalizados sem misturar representações",
)
def list_movements(
    process_id: UUID,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    occurrence_id: UUID | None = None,
) -> ProcessMovementsResponse:
    with request.app.state.session_factory() as session:
        _require_process(session, process_id)
        representations = session.scalars(
            select(Representation)
            .where(Representation.process_id == process_id)
            .order_by(Representation.id.asc())
        ).all()
        diagnostics, snapshots = _movement_context(session, representations)
        total = 0
        if occurrence_id is not None:
            selected = session.execute(
                select(MovementOccurrence, MovementSnapshotOccurrence)
                .join(
                    MovementSnapshotOccurrence,
                    MovementSnapshotOccurrence.occurrence_id == MovementOccurrence.id,
                )
                .join(
                    MovementSnapshot,
                    MovementSnapshot.id == MovementSnapshotOccurrence.snapshot_id,
                )
                .join(Representation, Representation.id == MovementOccurrence.representation_id)
                .where(
                    Representation.process_id == process_id,
                    MovementOccurrence.id == occurrence_id,
                    MovementSnapshotOccurrence.present.is_(True),
                )
                .order_by(MovementSnapshot.processed_at.desc(), MovementSnapshot.id.desc())
                .limit(1)
            ).one_or_none()
            rows = [selected] if selected is not None else []
            total = len(rows)
        elif snapshots:
            where = MovementSnapshotOccurrence.snapshot_id.in_(snapshots)
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(MovementSnapshotOccurrence)
                    .where(where, MovementSnapshotOccurrence.present.is_(True))
                )
                or 0
            )
            rows = session.execute(
                select(MovementOccurrence, MovementSnapshotOccurrence)
                .join(
                    MovementSnapshotOccurrence,
                    MovementSnapshotOccurrence.occurrence_id == MovementOccurrence.id,
                )
                .where(where, MovementSnapshotOccurrence.present.is_(True))
                .order_by(
                    MovementOccurrence.source_date_normalized.asc().nulls_last(),
                    MovementOccurrence.id.asc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        else:
            rows = []
        items = [
            ProcessMovementResponse(
                occurrence_id=occurrence.id,
                representation_id=occurrence.representation_id,
                content=occurrence.normalized_content,
                source_date_original=occurrence.source_date_original,
                source_date_status=occurrence.source_date_status,
                source_date=occurrence.source_date_normalized,
                multiplicity_ordinal=occurrence.multiplicity_ordinal,
                comparison_result=association.comparison_result,
                comparison_detail=association.comparison_detail,
                first_observed_at=occurrence.first_observed_at,
            )
            for occurrence, association in rows
        ]
    return ProcessMovementsResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        process_id=process_id,
        diagnostics=list(diagnostics.values()),
    )


def _load_process_summaries(
    session: Session,
    processes: Sequence[Process],
) -> list[ProcessSummaryResponse]:
    if not processes:
        return []
    process_ids = [process.id for process in processes]
    representations = session.scalars(
        select(Representation)
        .where(Representation.process_id.in_(process_ids))
        .order_by(Representation.process_id.asc(), Representation.id.asc())
    ).all()
    triage_records = session.scalars(
        select(ProcessTriage).where(ProcessTriage.process_id.in_(process_ids))
    ).all()
    triage_by_process = {triage.process_id: triage for triage in triage_records}
    by_process: dict[UUID, list[Representation]] = defaultdict(list)
    for representation in representations:
        by_process[representation.process_id].append(representation)
    latest_collections = _latest_collection_map(session, representations)
    responses: list[ProcessSummaryResponse] = []
    for process in processes:
        process_representations = by_process.get(process.id, [])
        latest_observed_at = max(
            (rep.last_observed_at for rep in process_representations if rep.last_observed_at),
            default=None,
        )
        latest_collection = max(
            (
                latest_collections[rep.id]
                for rep in process_representations
                if rep.id in latest_collections
            ),
            key=lambda value: value.included_at,
            default=None,
        )
        triage_state = state_from_record(triage_by_process.get(process.id))
        responses.append(
            ProcessSummaryResponse(
                id=process.id,
                numero_cnj=process.numero_cnj,
                created_at=process.created_at,
                representation_count=len(process_representations),
                latest_observed_at=latest_observed_at,
                latest_collection=latest_collection,
                triage=ProcessTriageStateResponse(
                    decision=triage_state.decision,
                    rural_link=triage_state.rural_link,
                    note=triage_state.note,
                    version=triage_state.version,
                    updated_at=triage_state.updated_at,
                ),
            )
        )
    return responses


def _load_representation_views(
    session: Session,
    representations: Sequence[Representation],
) -> list[ProcessRepresentationResponse]:
    if not representations:
        return []
    latest_collections = _latest_collection_map(session, representations)
    diagnostics, _ = _movement_context(session, representations)
    return [
        ProcessRepresentationResponse(
            id=representation.id,
            process_id=representation.process_id,
            source=representation.source,
            tribunal=representation.tribunal,
            source_id=representation.source_id,
            latest_version_id=representation.latest_version_id,
            class_code=representation.class_code,
            class_name=representation.class_name,
            grau=representation.grau,
            court_unit_code=representation.court_unit_code,
            court_unit_name=representation.court_unit_name,
            source_filed_at_original=representation.source_filed_at_original,
            source_filed_at=representation.source_filed_at,
            source_filed_at_timezone_ambiguous=representation.source_filed_at_timezone_ambiguous,
            source_updated_at_original=representation.source_updated_at_original,
            source_updated_at=representation.source_updated_at,
            source_updated_at_timezone_ambiguous=(
                representation.source_updated_at_timezone_ambiguous
            ),
            last_observed_at=representation.last_observed_at,
            latest_collection=latest_collections.get(representation.id),
            movement_diagnostic=diagnostics[representation.id],
        )
        for representation in representations
    ]


def _latest_collection_map(
    session: Session,
    representations: Sequence[Representation],
) -> dict[UUID, LatestCollectionResponse]:
    representation_ids = [representation.id for representation in representations]
    rows = session.execute(
        select(
            CollectionResult.representation_id,
            CollectionResult.collection_id,
            Collection.mode,
            CollectionResult.included_at,
            CollectionResult.capture_outcome,
            Job.id,
            Job.status,
        )
        .join(Collection, Collection.id == CollectionResult.collection_id)
        .outerjoin(Job, Job.collection_id == Collection.id)
        .where(CollectionResult.representation_id.in_(representation_ids))
        .order_by(
            CollectionResult.included_at.desc(),
            CollectionResult.collection_id.desc(),
        )
    ).all()
    result: dict[UUID, LatestCollectionResponse] = {}
    for representation_id, collection_id, mode, included_at, outcome, job_id, status in rows:
        result.setdefault(
            representation_id,
            LatestCollectionResponse(
                collection_id=collection_id,
                environment=cast(Any, mode),
                included_at=included_at,
                capture_outcome=cast(Any, outcome),
                job_id=job_id,
                job_status=cast(JobStatus, status),
            ),
        )
    return result


def _movement_context(
    session: Session,
    representations: Sequence[Representation],
) -> tuple[dict[UUID, MovementDiagnosticResponse], list[UUID]]:
    diagnostics = {
        representation.id: MovementDiagnosticResponse(
            representation_id=representation.id,
            available=False,
            is_complete=None,
            rejection_count=None,
            normalizer_version=None,
            processed_at=None,
        )
        for representation in representations
    }
    version_by_representation = {
        representation.id: representation.latest_version_id
        for representation in representations
        if representation.latest_version_id is not None
    }
    if not version_by_representation:
        return diagnostics, []
    rows = (
        session.execute(
            select(MovementSnapshot)
            .where(
                MovementSnapshot.representation_id.in_(version_by_representation),
                MovementSnapshot.version_id.in_(version_by_representation.values()),
            )
            .order_by(MovementSnapshot.processed_at.desc(), MovementSnapshot.id.desc())
        )
        .scalars()
        .all()
    )
    snapshot_by_representation: dict[UUID, MovementSnapshot] = {}
    for snapshot in rows:
        if version_by_representation.get(snapshot.representation_id) != snapshot.version_id:
            continue
        snapshot_by_representation.setdefault(snapshot.representation_id, snapshot)
    for representation_id, snapshot in snapshot_by_representation.items():
        diagnostics[representation_id] = MovementDiagnosticResponse(
            representation_id=representation_id,
            available=True,
            is_complete=snapshot.is_complete,
            rejection_count=snapshot.rejection_count,
            normalizer_version=snapshot.normalizer_version,
            processed_at=snapshot.processed_at,
        )
    return diagnostics, [snapshot.id for snapshot in snapshot_by_representation.values()]


def _require_process(session: Session, process_id: UUID) -> Process:
    process = session.get(Process, process_id)
    if process is None:
        raise HTTPException(status_code=404)
    return process
