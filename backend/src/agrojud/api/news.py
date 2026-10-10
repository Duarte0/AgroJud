"""Read and review deterministic historical observations for watched cases."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.process_filters import (
    ProcessFilters,
    SignalEnvironment,
    process_filters_dependency,
    process_predicates,
)
from agrojud.api.schemas import (
    NewsCategory,
    NewsProvenance,
    NewsStatus,
    PaginationResponse,
    ProcessNewsResponse,
    ProcessNewsStatusPatchRequest,
)
from agrojud.db.models import Process, ProcessNews, Representation

router = APIRouter(prefix="/api/v1/news", tags=["news"])


@router.get(
    "",
    response_model=PaginationResponse[ProcessNewsResponse],
    responses=ERROR_RESPONSES,
    summary="Lista novidades observadas nos processos acompanhados",
)
def list_news(
    request: Request,
    filters: Annotated[ProcessFilters, Depends(process_filters_dependency)],
    process_id: UUID | None = None,
    status: NewsStatus | None = None,
    category: NewsCategory | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> PaginationResponse[ProcessNewsResponse]:
    predicates = []
    if process_id is not None:
        predicates.append(ProcessNews.process_id == process_id)
    if status is not None:
        predicates.append(ProcessNews.status == status)
    if category is not None:
        predicates.append(ProcessNews.category == category)
    environment: SignalEnvironment = (
        "real" if request.app.state.settings.environment == "real" else "demo"
    )
    selected_processes = select(Process.id).where(
        *process_predicates(filters, environment=environment)
    )
    predicates.append(ProcessNews.process_id.in_(selected_processes))

    with request.app.state.session_factory() as session:
        total = (
            session.scalar(select(func.count()).select_from(ProcessNews).where(*predicates)) or 0
        )
        rows = session.execute(
            select(ProcessNews, Process, Representation)
            .join(Process, Process.id == ProcessNews.process_id)
            .join(Representation, Representation.id == ProcessNews.representation_id)
            .where(*predicates)
            .order_by(ProcessNews.first_observed_at.desc(), ProcessNews.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = [
            _news_response(news, process, representation) for news, process, representation in rows
        ]
    return PaginationResponse(items=items, page=page, page_size=page_size, total=total)


@router.patch(
    "/{news_id}",
    response_model=ProcessNewsResponse,
    responses=ERROR_RESPONSES,
    summary="Altera a situação de revisão de uma novidade",
)
def update_news_status(
    news_id: UUID,
    body: ProcessNewsStatusPatchRequest,
    request: Request,
) -> ProcessNewsResponse:
    with request.app.state.session_factory() as session, session.begin():
        news = session.scalar(
            select(ProcessNews).where(ProcessNews.id == news_id).with_for_update()
        )
        if news is None:
            raise HTTPException(status_code=404)
        news.status = body.status
        session.flush()
        process = session.get(Process, news.process_id)
        representation = session.get(Representation, news.representation_id)
        if process is None or representation is None:  # pragma: no cover - foreign keys retain both
            raise RuntimeError("A novidade perdeu sua origem local.")
        response = _news_response(news, process, representation)
    return response


def _news_response(
    news: ProcessNews,
    process: Process,
    representation: Representation,
) -> ProcessNewsResponse:
    return ProcessNewsResponse(
        id=news.id,
        process_id=news.process_id,
        numero_cnj=process.numero_cnj,
        representation_id=news.representation_id,
        source=representation.source,
        tribunal=representation.tribunal,
        source_id=representation.source_id,
        category=cast(NewsCategory, news.category),
        status=cast(NewsStatus, news.status),
        event_date=news.source_date,
        event_date_original=news.source_date_original,
        event_date_status=news.source_date_status,
        first_observed_at=news.first_observed_at,
        evidence=news.evidence,
        provenance=cast(NewsProvenance, news.provenance),
        created_at=news.created_at,
    )
