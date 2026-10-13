"""Availability and progress APIs for independently checkpointed tribunal groups."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import select

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.jobs import (
    SourceCapabilityUnavailable,
    _compile_discovery_criteria,
    _source_for_environment,
)
from agrojud.api.schemas import (
    CreateJobGroupRequest,
    DiscoveryJobGroupRequest,
    ExecutionGroupMemberResponse,
    ExecutionGroupResponse,
    RefreshNumberJobGroupRequest,
    TribunalAvailabilityItemResponse,
    TribunalAvailabilityResponse,
)
from agrojud.db.models import Process, ProcessWatchlistEntry
from agrojud.services.collection import build_collection_request
from agrojud.services.execution_groups import (
    ExecutionGroupView,
    execution_group_idempotency_key,
)
from agrojud.services.jobs import EnqueueRequest
from agrojud.sources.contracts import SourceError, SourceErrorCode, build_query_by_case_number
from agrojud.sources.factory import real_source_unavailable_reason

router = APIRouter(prefix="/api/v1", tags=["execution-groups"])


@router.get(
    "/tribunal-availability",
    response_model=TribunalAvailabilityResponse,
    responses=ERROR_RESPONSES,
    summary="Lista os tribunais disponíveis na allowlist DataJud",
)
def get_tribunal_availability(
    request: Request,
    preset_id: str | None = Query(default=None, min_length=1, max_length=120),
) -> TribunalAvailabilityResponse:
    snapshot = request.app.state.execution_groups.availability(preset_id=preset_id)
    return TribunalAvailabilityResponse(
        selection_enabled=snapshot.selection_enabled,
        preset_id=preset_id,
        matrix_sha256=snapshot.matrix_sha256,
        allowlist_version=snapshot.allowlist_version,
        catalog_version=snapshot.catalog_version,
        tpu_version=snapshot.tpu_version,
        global_reasons=list(snapshot.global_reasons),
        tribunals=[
            TribunalAvailabilityItemResponse(
                tribunal=item.tribunal,
                s1_s2_state=cast(Any, item.s1_s2_state),
                preset_state=cast(Any, item.preset_state),
                enabled=item.enabled,
                reason=item.reason,
            )
            for item in snapshot.tribunals
        ],
    )


@router.post(
    "/job-groups",
    response_model=ExecutionGroupResponse,
    status_code=202,
    responses=ERROR_RESPONSES,
    summary="Cria grupo de coleta com um job independente por tribunal",
)
def create_job_group(
    body: CreateJobGroupRequest,
    request: Request,
    response: Response,
) -> ExecutionGroupResponse:
    settings = request.app.state.settings
    mode, source = _source_for_environment(settings)
    unavailable = real_source_unavailable_reason(settings)
    if unavailable is not None:
        raise SourceCapabilityUnavailable(unavailable)

    if isinstance(body, DiscoveryJobGroupRequest):
        if body.criteria.preset_id is not None:
            preset_id = body.criteria.preset_id
        elif body.criteria.search_definition is not None:
            reference = body.criteria.search_definition
            preset_id = (
                reference.id if reference.kind == "official_theme" else f"custom:{reference.id}"
            )
        else:  # pragma: no cover - request schema enforces exactly one reference
            raise SourceError(SourceErrorCode.VALIDATION, "A definição da busca está ausente.")

        def make_request(tribunal: str) -> EnqueueRequest:
            compiled = _compile_discovery_criteria(
                request,
                body.criteria,
                mode=mode,
                tribunal=tribunal,
            )
            return build_collection_request(
                mode=mode,
                source=source,
                job_type=body.kind,
                query=compiled.query,
                catalog_snapshot=compiled.snapshot(),
                page_size=body.criteria.page_size,
                hit_budget=body.criteria.hit_budget,
            )

        criteria_snapshot = body.criteria.model_dump(mode="json")
    else:
        preset_id = None

        def make_request(tribunal: str) -> EnqueueRequest:
            query = build_query_by_case_number(body.criteria.process_number, tribunal=tribunal)
            return build_collection_request(
                mode=mode,
                source=source,
                job_type=body.kind,
                query=query,
                page_size=body.criteria.page_size,
                hit_budget=body.criteria.hit_budget,
            )

        criteria_snapshot = body.criteria.model_dump(mode="json")

    try:
        with request.app.state.session_factory() as session, session.begin():
            idempotency_key = None
            if isinstance(body, RefreshNumberJobGroupRequest):
                process_number = build_query_by_case_number(
                    body.criteria.process_number
                ).process_number
                process = session.scalar(
                    select(Process).where(Process.numero_cnj == process_number)
                )
                if process is None:
                    raise HTTPException(status_code=404)
                watch = session.get(ProcessWatchlistEntry, process.id)
                if watch is None or not watch.active:
                    raise HTTPException(status_code=409)
                idempotency_key = execution_group_idempotency_key(
                    target_id=process.id,
                    kind="refresh_number",
                    requests=[make_request(tribunal) for tribunal in body.tribunals],
                )
            created = request.app.state.execution_groups.create_in_session(
                session,
                kind=body.kind,
                mode=mode,
                source=source,
                tribunals=body.tribunals,
                preset_id=preset_id,
                request_snapshot={"criteria": criteria_snapshot},
                request_for_tribunal=make_request,
                idempotency_key=idempotency_key,
            )
            view = request.app.state.execution_groups.inspect_in_session(session, created.group_id)
            if view is None:  # pragma: no cover - just inserted in this transaction
                raise RuntimeError("O grupo criado não pôde ser lido.")
            payload = _group_response(view)
    except SourceError:
        raise
    response.headers["Location"] = str(
        request.url_for("get_job_group", group_id=str(created.group_id))
    )
    return payload


@router.get(
    "/job-groups/{group_id}",
    name="get_job_group",
    response_model=ExecutionGroupResponse,
    responses=ERROR_RESPONSES,
    summary="Consulta o estado e progresso por tribunal",
)
def get_job_group(group_id: UUID, request: Request) -> ExecutionGroupResponse:
    with request.app.state.session_factory() as session:
        view = request.app.state.execution_groups.inspect_in_session(session, group_id)
    if view is None:
        raise HTTPException(status_code=404)
    return _group_response(view)


@router.post(
    "/job-groups/{group_id}/cancel",
    response_model=ExecutionGroupResponse,
    responses=ERROR_RESPONSES,
    summary="Cancela os jobs filhos ainda ativos",
)
def cancel_job_group(group_id: UUID, request: Request) -> ExecutionGroupResponse:
    with request.app.state.session_factory() as session, session.begin():
        view = request.app.state.execution_groups.inspect_in_session(session, group_id)
        if view is None:
            raise HTTPException(status_code=404)
        request.app.state.execution_groups.cancel_in_session(session, group_id)
    with request.app.state.session_factory() as session:
        view = request.app.state.execution_groups.inspect_in_session(session, group_id)
    if view is None:  # pragma: no cover - group rows are protected by RESTRICT constraints
        raise HTTPException(status_code=404)
    return _group_response(view)


def _group_response(view: ExecutionGroupView) -> ExecutionGroupResponse:
    return ExecutionGroupResponse(
        id=view.id,
        kind=view.kind,
        state=cast(Any, view.state),
        matrix_sha256=view.matrix_sha256,
        allowlist_version=view.allowlist_version,
        catalog_version=view.catalog_version,
        tpu_version=view.tpu_version,
        request_snapshot=view.request_snapshot,
        member_count=view.member_count,
        completed_count=view.completed_count,
        created_at=view.created_at,
        updated_at=view.updated_at,
        finished_at=view.finished_at,
        members=[
            ExecutionGroupMemberResponse(
                id=item.id,
                tribunal=item.tribunal,
                state=item.state,
                availability_state=item.availability_state,
                reason=item.reason,
                job_id=item.job_id,
                collection_id=item.collection_id,
                coverage=item.coverage,
                next_page=item.next_page,
                checkpoint_revision=item.checkpoint_revision,
                checkpoint_updated_at=item.checkpoint_updated_at,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in view.members
        ],
    )
