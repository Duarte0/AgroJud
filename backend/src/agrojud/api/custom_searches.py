"""CRUD API for immutable revisions of user-defined structured searches."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.schemas import (
    CustomSearchArchiveResponse,
    CustomSearchCreateRequest,
    CustomSearchCriteriaResponse,
    CustomSearchPatchRequest,
    CustomSearchResponse,
    CustomSearchVersionResponse,
    TPUCodeSelectionResponse,
)
from agrojud.db.models import CustomSearch, CustomSearchVersion
from agrojud.services.custom_searches import (
    CustomSearchArchivedError,
    CustomSearchNotFoundError,
    CustomSearchService,
)
from agrojud.sources.catalog import load_catalog
from agrojud.sources.contracts import SourceError

router = APIRouter(prefix="/api/v1/custom-searches", tags=["custom-searches"])


@router.get("", response_model=list[CustomSearchResponse], responses=ERROR_RESPONSES)
def list_custom_searches(
    request: Request,
    include_archived: bool = Query(default=False),
) -> list[CustomSearchResponse]:
    service: CustomSearchService = request.app.state.custom_searches
    with request.app.state.session_factory() as session:
        searches = service.list_searches(session, include_archived=include_archived)
        return [_current_response(service, session, search) for search in searches]


@router.post(
    "",
    response_model=CustomSearchResponse,
    status_code=201,
    responses=ERROR_RESPONSES,
    summary="Cria uma busca personalizada estruturada e versionada",
)
def create_custom_search(
    body: CustomSearchCreateRequest,
    request: Request,
) -> CustomSearchResponse:
    mode = "real" if request.app.state.settings.environment == "real" else "demo"
    service: CustomSearchService = request.app.state.custom_searches
    try:
        with request.app.state.session_factory() as session, session.begin():
            search, revision = service.create(
                session,
                name=body.name,
                description=body.description,
                class_codes=body.class_codes,
                subject_codes=body.subject_codes,
                dimension_combination=cast(Any, body.dimension_combination),
                mode=cast(Any, mode),
                copy_from_theme=body.copy_from_theme,
                criteria_provided=bool(
                    {"class_codes", "subject_codes", "dimension_combination"}
                    & body.model_fields_set
                ),
            )
            return _response(search, revision)
    except SourceError:
        raise


@router.get(
    "/{search_id}/versions",
    response_model=list[CustomSearchVersionResponse],
    responses=ERROR_RESPONSES,
    summary="Lista versões históricas sem reescrevê-las",
)
def list_custom_search_versions(
    search_id: UUID, request: Request
) -> list[CustomSearchVersionResponse]:
    service: CustomSearchService = request.app.state.custom_searches
    try:
        with request.app.state.session_factory() as session:
            service.version(session, search_id, include_archived=True)
            revisions = session.scalars(
                select(CustomSearchVersion)
                .where(CustomSearchVersion.custom_search_id == search_id)
                .order_by(CustomSearchVersion.version.desc())
            ).all()
            return [_version_response(revision) for revision in revisions]
    except CustomSearchNotFoundError:
        raise HTTPException(status_code=404, detail="A busca personalizada não existe.") from None


@router.patch(
    "/{search_id}",
    response_model=CustomSearchResponse,
    responses=ERROR_RESPONSES,
    summary="Edita uma busca criando uma nova versão",
)
def patch_custom_search(
    search_id: UUID,
    body: CustomSearchPatchRequest,
    request: Request,
) -> CustomSearchResponse:
    service: CustomSearchService = request.app.state.custom_searches
    mode = "real" if request.app.state.settings.environment == "real" else "demo"
    fields = body.model_fields_set
    try:
        with request.app.state.session_factory() as session, session.begin():
            search, revision = service.patch(
                session,
                search_id,
                name=body.name if "name" in fields else None,
                description=body.description if "description" in fields else None,
                description_provided="description" in fields,
                class_codes=body.class_codes if "class_codes" in fields else None,
                subject_codes=body.subject_codes if "subject_codes" in fields else None,
                dimension_combination=(
                    cast(Any, body.dimension_combination)
                    if "dimension_combination" in fields
                    else None
                ),
                mode=cast(Any, mode),
            )
            return _response(search, revision)
    except CustomSearchNotFoundError:
        raise HTTPException(status_code=404, detail="A busca personalizada não existe.") from None
    except CustomSearchArchivedError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except SourceError:
        raise


@router.post(
    "/{search_id}/archive",
    response_model=CustomSearchArchiveResponse,
    responses=ERROR_RESPONSES,
    summary="Arquiva uma busca sem remover versões nem coletas",
)
def archive_custom_search(search_id: UUID, request: Request) -> CustomSearchArchiveResponse:
    service: CustomSearchService = request.app.state.custom_searches
    try:
        with request.app.state.session_factory() as session, session.begin():
            search = service.archive(session, search_id)
            if search.archived_at is None:  # pragma: no cover - archive always sets it
                raise RuntimeError("Busca arquivada sem instante de arquivamento.")
            return CustomSearchArchiveResponse(id=search.id, archived_at=search.archived_at)
    except CustomSearchNotFoundError:
        raise HTTPException(status_code=404, detail="A busca personalizada não existe.") from None


def _current_response(
    service: CustomSearchService,
    session: Session,
    search: CustomSearch,
) -> CustomSearchResponse:
    return _response(search, service.version(session, search.id, search.current_version))


def _response(search: CustomSearch, revision: CustomSearchVersion) -> CustomSearchResponse:
    criteria = _criteria_response(revision)
    return CustomSearchResponse(
        id=search.id,
        version=revision.version,
        name=revision.name,
        description=revision.description,
        criteria=criteria,
        archived_at=search.archived_at,
        created_at=search.created_at,
        updated_at=search.updated_at,
    )


def _version_response(revision: CustomSearchVersion) -> CustomSearchVersionResponse:
    return CustomSearchVersionResponse(
        id=revision.custom_search_id,
        version=revision.version,
        name=revision.name,
        description=revision.description,
        criteria=_criteria_response(revision),
        created_at=revision.created_at,
    )


def _criteria_response(revision: CustomSearchVersion) -> CustomSearchCriteriaResponse:
    catalog = load_catalog()
    return CustomSearchCriteriaResponse(
        classes=[
            TPUCodeSelectionResponse(
                code=code,
                name=catalog.codes[("class", code)].name,
            )
            for code in revision.class_codes
        ],
        subjects=[
            TPUCodeSelectionResponse(
                code=code,
                name=catalog.codes[("subject", code)].name,
            )
            for code in revision.subject_codes
        ],
        dimension_combination=cast(Any, revision.dimension_combination),
    )
