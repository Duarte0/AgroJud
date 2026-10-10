"""Consistent read-only aggregates for the locally persisted process sample."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import distinct, func, select, text
from sqlalchemy.sql import Select
from sqlalchemy.sql.selectable import CTE

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.process_filters import (
    ProcessFilters,
    SignalEnvironment,
    current_signal_rule_predicate,
    process_filters_dependency,
    process_predicates,
)
from agrojud.api.schemas import (
    OverviewCollectionResponse,
    OverviewFiltersResponse,
    OverviewMetricResponse,
    OverviewResponse,
    OverviewSignalCategoryResponse,
    OverviewThemeResponse,
    OverviewTriageResponse,
)
from agrojud.db.models import (
    Job,
    Process,
    ProcessNews,
    ProcessSignal,
    ProcessTriage,
    ProcessWatchlistEntry,
    Representation,
    RepresentationSubject,
)

router = APIRouter(tags=["overview"])


@router.get(
    "/api/v1/overview",
    response_model=OverviewResponse,
    responses=ERROR_RESPONSES,
    summary="Resume indicadores descritivos da base local",
)
def get_overview(
    request: Request,
    filters: Annotated[ProcessFilters, Depends(process_filters_dependency)],
) -> OverviewResponse:
    environment: SignalEnvironment = (
        "real" if request.app.state.settings.environment == "real" else "demo"
    )
    selected_processes = _selected_processes(filters, environment)
    with request.app.state.session_factory() as session, session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        totals = session.execute(_overview_statistics_statement(selected_processes)).one()
        signal_rows = session.execute(
            _signal_distribution_statement(selected_processes, environment)
        ).all()
        theme_rows = session.execute(_theme_distribution_statement(selected_processes)).all()
        collection_rows = session.execute(_latest_collections_statement()).all()

    source = "datajud" if environment == "real" else "synthetic"
    return OverviewResponse(
        generated_at=totals.generated_at,
        data_source=cast(Any, source),
        filters=_filters_response(filters),
        processes=_metric(totals.process_count, "processes"),
        representations=_metric(totals.representation_count, "representations"),
        triage=OverviewTriageResponse(
            pending=_metric(totals.triage_pending, "processes"),
            relevant=_metric(totals.triage_relevant, "processes"),
            discarded=_metric(totals.triage_discarded, "processes"),
        ),
        followed_processes=_metric(totals.followed_process_count, "processes"),
        pending_news=_metric(totals.pending_news_count, "occurrences"),
        current_signals=[
            OverviewSignalCategoryResponse(
                category=row.category,
                processes=_metric(row.process_count, "processes"),
            )
            for row in signal_rows
        ],
        themes=[
            OverviewThemeResponse(
                subject_code=row.subject_code,
                subject_name=row.subject_name,
                processes=_metric(row.process_count, "processes"),
            )
            for row in theme_rows
        ],
        latest_collections=[
            OverviewCollectionResponse(
                collection_id=row.collection_id,
                job_id=row.job_id,
                environment=cast(Any, row.mode),
                status=cast(Any, row.status),
                created_at=row.created_at,
            )
            for row in collection_rows
        ],
        latest_observation_at=totals.latest_observation_at,
    )


def _selected_processes(
    filters: ProcessFilters,
    environment: SignalEnvironment,
) -> CTE:
    return (
        select(Process.id.label("process_id"))
        .where(*process_predicates(filters, environment=environment))
        .cte("selected_processes")
    )


def _overview_statistics_statement(selected_processes: CTE) -> Select[Any]:
    selected_ids = select(selected_processes.c.process_id)
    active_followed = (
        select(func.count(distinct(ProcessWatchlistEntry.process_id)))
        .where(
            ProcessWatchlistEntry.active.is_(True),
            ProcessWatchlistEntry.process_id.in_(selected_ids),
        )
        .scalar_subquery()
    )
    pending_news = (
        select(func.count(ProcessNews.id))
        .where(
            ProcessNews.status == "pending",
            ProcessNews.process_id.in_(selected_ids),
        )
        .scalar_subquery()
    )
    process_count = func.count(distinct(selected_processes.c.process_id))
    return (
        select(
            func.transaction_timestamp().label("generated_at"),
            process_count.label("process_count"),
            func.count(distinct(Representation.id)).label("representation_count"),
            process_count.filter(
                func.coalesce(ProcessTriage.decision, "pending") == "pending"
            ).label("triage_pending"),
            process_count.filter(
                func.coalesce(ProcessTriage.decision, "pending") == "relevant"
            ).label("triage_relevant"),
            process_count.filter(
                func.coalesce(ProcessTriage.decision, "pending") == "discarded"
            ).label("triage_discarded"),
            active_followed.label("followed_process_count"),
            pending_news.label("pending_news_count"),
            func.max(Representation.last_observed_at).label("latest_observation_at"),
        )
        .select_from(selected_processes)
        .outerjoin(ProcessTriage, ProcessTriage.process_id == selected_processes.c.process_id)
        .outerjoin(Representation, Representation.process_id == selected_processes.c.process_id)
    )


def _signal_distribution_statement(
    selected_processes: CTE,
    environment: SignalEnvironment,
) -> Select[str, int]:
    return (
        select(
            ProcessSignal.category.label("category"),
            func.count(distinct(ProcessSignal.process_id)).label("process_count"),
        )
        .where(
            ProcessSignal.process_id.in_(select(selected_processes.c.process_id)),
            current_signal_rule_predicate(environment),
        )
        .group_by(ProcessSignal.category)
        .order_by(ProcessSignal.category.asc())
    )


def _theme_distribution_statement(
    selected_processes: CTE,
) -> Select[str | None, str | None, int]:
    process_count = func.count(distinct(Representation.process_id))
    return (
        select(
            RepresentationSubject.subject_code.label("subject_code"),
            RepresentationSubject.subject_name.label("subject_name"),
            process_count.label("process_count"),
        )
        .join(
            Representation,
            Representation.id == RepresentationSubject.representation_id,
        )
        .where(Representation.process_id.in_(select(selected_processes.c.process_id)))
        .group_by(RepresentationSubject.subject_code, RepresentationSubject.subject_name)
        .order_by(
            process_count.desc(),
            RepresentationSubject.subject_code.asc().nulls_last(),
            RepresentationSubject.subject_name.asc().nulls_last(),
        )
    )


def _latest_collections_statement() -> Select[UUID | None, UUID, str, str, datetime]:
    return (
        select(
            Job.collection_id.label("collection_id"),
            Job.id.label("job_id"),
            Job.mode.label("mode"),
            Job.status.label("status"),
            Job.created_at.label("created_at"),
        )
        .where(Job.collection_id.is_not(None))
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(5)
    )


def _filters_response(filters: ProcessFilters) -> OverviewFiltersResponse:
    return OverviewFiltersResponse.model_validate(
        {
            "process_number": filters.process_number,
            "subject": filters.subject,
            "subject_code": filters.subject_code,
            "subject_name_exact": filters.subject_name_exact,
            "class": filters.class_filter,
            "court_unit": filters.court_unit,
            "collection_id": filters.collection_id,
            "preset_id": filters.preset_id,
            "decision": filters.decision,
            "rural_link": filters.rural_link,
            "followed": filters.followed,
            "pending_news": filters.pending_news,
            "signal_category": filters.signal_category,
        }
    )


def _metric(value: int, unit: str) -> OverviewMetricResponse:
    return OverviewMetricResponse(value=value, unit=cast(Any, unit))
