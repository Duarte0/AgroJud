"""Saved-search commands and schedule status for the local operator UI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.jobs import _source_for_environment
from agrojud.api.schedule_status import schedule_dispatch_response
from agrojud.api.schemas import (
    JobCreatedResponse,
    JobStatus,
    SavedSearchAvailabilityResponse,
    SavedSearchCreateRequest,
    SavedSearchFilters,
    SavedSearchPatchRequest,
    SavedSearchResponse,
)
from agrojud.db.models import SavedSearch, SavedSearchVersion, ScheduleDispatch
from agrojud.services.saved_searches import (
    SavedSearchNotFoundError,
    availability_reasons,
)
from agrojud.sources.contracts import SourceError

router = APIRouter(prefix="/api/v1/saved-searches", tags=["saved-searches"])


@router.post(
    "",
    response_model=SavedSearchResponse,
    status_code=201,
    responses=ERROR_RESPONSES,
    summary="Salva uma busca versionada e agenda sua próxima atualização",
)
def create_saved_search(body: SavedSearchCreateRequest, request: Request) -> SavedSearchResponse:
    mode, source = _source_for_environment(request.app.state.settings)
    try:
        with request.app.state.session_factory() as session, session.begin():
            search, revision = request.app.state.saved_searches.create(
                session,
                name=body.name,
                preset_id=body.preset_id,
                window_mode=body.window_mode,
                filters=body.filters.model_dump(),
                enabled=body.enabled,
                mode=mode,
            )
            response = _search_response(
                session,
                search,
                revision,
                mode=mode,
                source=source,
                reference_time=datetime.now(UTC),
            )
    except SourceError:
        raise
    return response


@router.get(
    "",
    response_model=list[SavedSearchResponse],
    responses=ERROR_RESPONSES,
    summary="Lista buscas salvas, versão atual e situação da agenda",
)
def list_saved_searches(request: Request) -> list[SavedSearchResponse]:
    mode, source = _source_for_environment(request.app.state.settings)
    with request.app.state.session_factory() as session:
        searches = session.scalars(
            select(SavedSearch).order_by(SavedSearch.created_at.desc(), SavedSearch.id)
        ).all()
        return [
            _search_response(
                session,
                search,
                request.app.state.saved_searches.current_revision(session, search),
                mode=mode,
                source=source,
                reference_time=datetime.now(UTC),
            )
            for search in searches
        ]


@router.patch(
    "/{search_id}",
    response_model=SavedSearchResponse,
    responses=ERROR_RESPONSES,
    summary="Altera uma busca salva sem reescrever revisões anteriores",
)
def patch_saved_search(
    search_id: UUID,
    body: SavedSearchPatchRequest,
    request: Request,
) -> SavedSearchResponse:
    mode, source = _source_for_environment(request.app.state.settings)
    fields = body.model_fields_set
    try:
        with request.app.state.session_factory() as session, session.begin():
            search, revision = request.app.state.saved_searches.patch(
                session,
                search_id,
                name=body.name if "name" in fields else None,
                preset_id=body.preset_id if "preset_id" in fields else None,
                window_mode=body.window_mode if "window_mode" in fields else None,
                filters=body.filters.model_dump() if body.filters is not None else None,
                enabled=body.enabled if "enabled" in fields else None,
                mode=mode,
            )
            response = _search_response(
                session,
                search,
                revision,
                mode=mode,
                source=source,
                reference_time=datetime.now(UTC),
            )
    except SavedSearchNotFoundError:
        raise HTTPException(status_code=404, detail="A busca salva não existe.") from None
    return response


@router.post(
    "/{search_id}/run",
    response_model=JobCreatedResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Inicia explicitamente uma execução manual da busca salva",
)
def run_saved_search(
    search_id: UUID,
    request: Request,
    response: Response,
) -> JobCreatedResponse:
    mode, source = _source_for_environment(request.app.state.settings)
    try:
        with request.app.state.session_factory() as session, session.begin():
            result = request.app.state.saved_searches.manual_run(
                session, search_id, mode=mode, source=source
            )
    except SavedSearchNotFoundError:
        raise HTTPException(status_code=404, detail="A busca salva não existe.") from None
    inspection = request.app.state.jobs.inspect(result.job_id)
    response.headers["Location"] = str(request.url_for("get_job", job_id=str(result.job_id)))
    return JobCreatedResponse(
        job_id=result.job_id,
        collection_id=result.collection_id,
        status=cast(JobStatus, inspection.status),
        reused=not result.created,
    )


def _search_response(
    session: Session,
    search: SavedSearch,
    revision: SavedSearchVersion,
    *,
    mode: str,
    source: str,
    reference_time: datetime,
) -> SavedSearchResponse:
    dispatch = session.scalar(
        select(ScheduleDispatch)
        .where(ScheduleDispatch.saved_search_id == search.id)
        .order_by(
            ScheduleDispatch.scheduled_for_date.desc(),
            ScheduleDispatch.created_at.desc(),
            ScheduleDispatch.id.desc(),
        )
        .limit(1)
    )
    reasons = availability_reasons(
        revision,
        search_id=search.id,
        mode=cast(Any, mode),
        source=source,
        reference_time=reference_time,
    )
    return SavedSearchResponse(
        id=search.id,
        name=search.name,
        version=revision.version,
        preset_id=revision.preset_id,
        preset_version=revision.preset_version,
        window_mode=cast(Any, revision.window_mode),
        filters=SavedSearchFilters.model_validate(revision.filters),
        enabled=search.enabled,
        next_run_at=search.next_run_at,
        availability=SavedSearchAvailabilityResponse(
            enabled=not reasons,
            reasons=reasons,
        ),
        last_dispatch=schedule_dispatch_response(dispatch),
        created_at=search.created_at,
        updated_at=search.updated_at,
    )
