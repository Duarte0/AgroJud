"""Read-only suggestions for textual filters over the current local environment."""

from __future__ import annotations

import unicodedata
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from sqlalchemy import case, distinct, func, or_, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.process_filters import SignalEnvironment, current_signal_rule_predicate
from agrojud.api.schemas import (
    ProcessFilterField,
    ProcessFilterOptionResponse,
    ProcessFilterOptionsResponse,
)
from agrojud.db.models import (
    Collection,
    CollectionResult,
    Job,
    Process,
    ProcessSignal,
    Representation,
    RepresentationSubject,
)

router = APIRouter(tags=["processes"])

_ACCENTED = "áàâãäåçéèêëíìîïñóòôõöúùûüýÿ"
_UNACCENTED = "aaaaaaceeeeiiiinooooouuuuyy"
_LOCAL_ZONE = ZoneInfo("America/Sao_Paulo")


@router.get(
    "/api/v1/process-filter-options",
    response_model=ProcessFilterOptionsResponse,
    responses=ERROR_RESPONSES,
    summary="Sugere valores locais para filtros de processos",
)
def list_process_filter_options(
    request: Request,
    field: ProcessFilterField,
    q: str = Query(default="", max_length=160),
    selected_value: str | None = Query(default=None, max_length=160),
) -> ProcessFilterOptionsResponse:
    """Return common values or text matches without applying active process filters."""

    environment: SignalEnvironment = (
        "real" if request.app.state.settings.environment == "real" else "demo"
    )
    normalized_query = _normalize_query(q)
    if field == "process_number":
        normalized_query = "".join(
            character for character in normalized_query if character.isdigit()
        )
    limit = 20 if normalized_query else 10

    with request.app.state.session_factory() as session, session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        if field == "collection_id":
            items = _collection_options(
                session,
                environment=environment,
                normalized_query=normalized_query,
                selected_value=selected_value,
                limit=limit,
            )
        elif field == "process_number" and not normalized_query and not selected_value:
            items = []
        else:
            items = _text_options(
                session,
                field=field,
                environment=environment,
                normalized_query=normalized_query,
                selected_value=selected_value,
                limit=limit,
            )
    return ProcessFilterOptionsResponse(field=field, items=items)


def _text_options(
    session: Session,
    *,
    field: ProcessFilterField,
    environment: SignalEnvironment,
    normalized_query: str,
    selected_value: str | None,
    limit: int,
) -> list[ProcessFilterOptionResponse]:
    candidates = _candidate_rows(field, environment).subquery("filter_option_candidates")
    value = candidates.c.value
    predicates = [value.is_not(None), func.btrim(value) != ""]
    match_predicates = []
    if normalized_query:
        match_predicates.append(
            _normalized_text(value).like(_like_pattern(normalized_query), escape="\\")
        )
    if selected_value:
        match_predicates.append(value == selected_value)
    if match_predicates:
        predicates.append(or_(*match_predicates))

    ordering = []
    if selected_value:
        ordering.append(case((value == selected_value, 0), else_=1))
    process_count = func.count(distinct(candidates.c.process_id))
    rows = session.execute(
        select(value, process_count.label("process_count"))
        .where(*predicates)
        .group_by(value)
        .order_by(*ordering, process_count.desc(), value.asc())
        .limit(limit)
    ).all()
    return [
        ProcessFilterOptionResponse(
            value=str(row.value),
            label=_option_label(field, str(row.value)),
            process_count=row.process_count,
        )
        for row in rows
    ]


def _candidate_rows(
    field: ProcessFilterField,
    environment: SignalEnvironment,
) -> Any:
    process_number = select(Process.numero_cnj.label("value"), Process.id.label("process_id"))
    if field == "process_number":
        return process_number

    if field == "subject":
        names = _representation_value_rows(RepresentationSubject.subject_name, subject=True)
        codes = _representation_value_rows(RepresentationSubject.subject_code, subject=True)
        return names.union_all(codes)
    if field == "subject_code":
        return _representation_value_rows(RepresentationSubject.subject_code, subject=True)
    if field == "subject_name_exact":
        return _representation_value_rows(RepresentationSubject.subject_name, subject=True)
    if field == "class":
        return _representation_value_rows(Representation.class_name).union_all(
            _representation_value_rows(Representation.class_code)
        )
    if field == "court_unit":
        return _representation_value_rows(Representation.court_unit_name).union_all(
            _representation_value_rows(Representation.court_unit_code)
        )
    if field == "preset_id":
        preset_id = Job.parameters_snapshot["query"]["preset_id"].astext
        return (
            select(preset_id.label("value"), Process.id.label("process_id"))
            .select_from(Process)
            .join(Representation, Representation.process_id == Process.id)
            .join(CollectionResult, CollectionResult.representation_id == Representation.id)
            .join(Collection, Collection.id == CollectionResult.collection_id)
            .join(Job, Job.collection_id == CollectionResult.collection_id)
            .where(Job.mode == environment, Collection.mode == environment, preset_id.is_not(None))
        )
    if field == "signal_category":
        return select(
            ProcessSignal.category.label("value"), ProcessSignal.process_id.label("process_id")
        ).where(current_signal_rule_predicate(environment))
    raise ValueError(f"Campo de filtro textual não suportado: {field}")


def _representation_value_rows(value: Any, *, subject: bool = False) -> Select[Any]:
    statement = (
        select(value.label("value"), Process.id.label("process_id"))
        .select_from(Process)
        .join(Representation, Representation.process_id == Process.id)
    )
    if subject:
        statement = statement.join(
            RepresentationSubject, RepresentationSubject.representation_id == Representation.id
        )
    return statement


def _collection_options(
    session: Session,
    *,
    environment: SignalEnvironment,
    normalized_query: str,
    selected_value: str | None,
    limit: int,
) -> list[ProcessFilterOptionResponse]:
    preset_id = Collection.resolved_criteria["query"]["preset_id"].astext
    display_date = func.to_char(
        func.timezone("America/Sao_Paulo", Collection.created_at), "DD/MM/YYYY HH24:MI"
    )
    display_status = case(
        (Job.status == "queued", "Na fila"),
        (Job.status == "running", "Em andamento"),
        (Job.status == "retry_wait", "Aguardando nova tentativa"),
        (Job.status == "completed", "Concluída"),
        (Job.status == "partial", "Parcial"),
        (Job.status == "failed", "Falhou"),
        (Job.status == "cancelled", "Cancelada"),
        else_="Sem execução",
    )
    process_count = func.count(distinct(Process.id))
    statement = (
        select(
            Collection.id.label("value"),
            Collection.created_at.label("created_at"),
            preset_id.label("preset_id"),
            display_status.label("status"),
            process_count.label("process_count"),
        )
        .select_from(Collection)
        .join(CollectionResult, CollectionResult.collection_id == Collection.id)
        .join(Representation, Representation.id == CollectionResult.representation_id)
        .join(Process, Process.id == Representation.process_id)
        .outerjoin(Job, Job.collection_id == Collection.id)
        .where(Collection.mode == environment)
        .group_by(Collection.id, Collection.created_at, preset_id, Job.status)
    )
    predicates = []
    if normalized_query:
        pattern = _like_pattern(normalized_query)
        predicates.append(
            or_(
                _normalized_text(func.coalesce(preset_id, "Consulta avulsa")).like(
                    pattern, escape="\\"
                ),
                _normalized_text(display_status).like(pattern, escape="\\"),
                _normalized_text(display_date).like(pattern, escape="\\"),
            )
        )
    selected_uuid = _parse_uuid(selected_value)
    if selected_uuid is not None:
        predicates.append(Collection.id == selected_uuid)
    if predicates:
        statement = statement.having(or_(*predicates))
    ordering = []
    if selected_uuid is not None:
        ordering.append(case((Collection.id == selected_uuid, 0), else_=1))
    rows = session.execute(
        statement.order_by(*ordering, Collection.created_at.desc(), Collection.id.desc()).limit(
            limit
        )
    ).all()

    results = []
    for row in rows:
        preset = row.preset_id or "Consulta avulsa"
        local_created_at = row.created_at.astimezone(_LOCAL_ZONE)
        date_label = local_created_at.strftime("%d/%m/%Y %H:%M")
        results.append(
            ProcessFilterOptionResponse(
                value=str(row.value),
                label=f"{date_label} · {preset} · {row.status}",
                detail=_process_count_detail(row.process_count),
                process_count=row.process_count,
            )
        )
    return results


def _option_label(field: ProcessFilterField, value: str) -> str:
    if field == "process_number" and len(value) == 20 and value.isdigit():
        return f"{value[:7]}-{value[7:9]}.{value[9:13]}.{value[13]}.{value[14:16]}.{value[16:20]}"
    return value


def _normalize_query(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _normalized_text(value: Any) -> Any:
    return func.translate(func.lower(value), _ACCENTED, _UNACCENTED)


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _parse_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _process_count_detail(count: int) -> str:
    return f"{count} processo" if count == 1 else f"{count} processos"
