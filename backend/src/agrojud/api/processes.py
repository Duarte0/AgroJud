"""Read-only process, representation, and movement queries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from agrojud.api.errors import ERROR_RESPONSES
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
)
from agrojud.db.models import (
    Collection,
    CollectionResult,
    Job,
    MovementOccurrence,
    MovementSnapshot,
    MovementSnapshotOccurrence,
    Process,
    Representation,
    RepresentationSubject,
)
from agrojud.sources.contracts import build_query_by_case_number

router = APIRouter(prefix="/api/v1/processes", tags=["processes"])


@router.get(
    "",
    response_model=PaginationResponse[ProcessSummaryResponse],
    responses=ERROR_RESPONSES,
    summary="Pesquisa processos persistidos",
)
def list_processes(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    process_number: str | None = Query(default=None, min_length=1, max_length=30),
    subject: str | None = Query(default=None, min_length=1, max_length=160),
    class_filter: str | None = Query(default=None, alias="class", min_length=1, max_length=160),
    court_unit: str | None = Query(default=None, min_length=1, max_length=160),
    collection_id: UUID | None = None,
    preset_id: str | None = Query(default=None, min_length=1, max_length=120),
) -> PaginationResponse[ProcessSummaryResponse]:
    predicates = _process_predicates(
        process_number=process_number,
        subject=subject,
        class_filter=class_filter,
        court_unit=court_unit,
        collection_id=collection_id,
        preset_id=preset_id,
    )
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
        if snapshots:
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


def _process_predicates(
    *,
    process_number: str | None,
    subject: str | None,
    class_filter: str | None,
    court_unit: str | None,
    collection_id: UUID | None,
    preset_id: str | None,
) -> list[Any]:
    predicates: list[Any] = []
    if process_number is not None:
        normalized = build_query_by_case_number(process_number).process_number
        predicates.append(Process.numero_cnj == normalized)

    representation_filters: list[Any] = []
    if subject is not None:
        subject_pattern = _contains_pattern(subject)
        representation_filters.append(
            exists(
                select(RepresentationSubject.id).where(
                    RepresentationSubject.representation_id == Representation.id,
                    or_(
                        RepresentationSubject.subject_name.ilike(subject_pattern, escape="\\"),
                        RepresentationSubject.subject_code.ilike(subject_pattern, escape="\\"),
                    ),
                )
            )
        )
    if class_filter is not None:
        class_pattern = _contains_pattern(class_filter)
        representation_filters.append(
            or_(
                Representation.class_name.ilike(class_pattern, escape="\\"),
                Representation.class_code.ilike(class_pattern, escape="\\"),
            )
        )
    if court_unit is not None:
        court_pattern = _contains_pattern(court_unit)
        representation_filters.append(
            or_(
                Representation.court_unit_name.ilike(court_pattern, escape="\\"),
                Representation.court_unit_code.ilike(court_pattern, escape="\\"),
            )
        )
    if collection_id is not None or preset_id is not None:
        collection_filters: list[Any] = [CollectionResult.representation_id == Representation.id]
        if collection_id is not None:
            collection_filters.append(CollectionResult.collection_id == collection_id)
        if preset_id is not None:
            collection_filters.append(
                Job.parameters_snapshot["query"]["preset_id"].astext == preset_id
            )
        representation_filters.append(
            exists(
                select(CollectionResult.id)
                .join(Job, Job.collection_id == CollectionResult.collection_id)
                .where(*collection_filters)
            )
        )

    if representation_filters:
        predicates.append(
            exists(
                select(Representation.id).where(
                    Representation.process_id == Process.id,
                    *representation_filters,
                )
            )
        )
    return predicates


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
        responses.append(
            ProcessSummaryResponse(
                id=process.id,
                numero_cnj=process.numero_cnj,
                created_at=process.created_at,
                representation_count=len(process_representations),
                latest_observed_at=latest_observed_at,
                latest_collection=latest_collection,
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


def _contains_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
