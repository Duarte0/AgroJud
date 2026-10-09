"""Versioned saved-search persistence and immutable collection preparation."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from agrojud.db.models import SavedSearch, SavedSearchVersion, ScheduleDispatch
from agrojud.services.collection import build_collection_request
from agrojud.services.jobs import EnqueueRequest, EnqueueResult, JobService
from agrojud.services.schedule_time import next_daily_occurrence
from agrojud.sources.catalog import compile_preset
from agrojud.sources.contracts import SourceError, SourceErrorCode

type WindowMode = Literal["fixed", "rolling_12_months"]
type SearchMode = Literal["demo", "real"]


class SavedSearchNotFoundError(LookupError):
    """A saved-search identifier does not exist."""


class SavedSearchService:
    """Store immutable criteria revisions and enqueue explicit manual runs."""

    def __init__(self, sessions: sessionmaker[Session], jobs: JobService) -> None:
        self.sessions = sessions
        self.jobs = jobs

    def create(
        self,
        session: Session,
        *,
        name: str,
        preset_id: str,
        window_mode: WindowMode,
        filters: dict[str, Any],
        enabled: bool,
        mode: SearchMode,
    ) -> tuple[SavedSearch, SavedSearchVersion]:
        now = _database_now(session)
        normalized_name = _name(name)
        revision = _make_revision(
            saved_search_id=uuid4(),
            version=1,
            name=normalized_name,
            preset_id=preset_id,
            window_mode=window_mode,
            filters=filters,
            mode=mode,
            reference_time=now,
        )
        search = SavedSearch(
            id=revision.saved_search_id,
            name=normalized_name,
            enabled=enabled,
            current_version=1,
            next_run_at=next_daily_occurrence(now) if enabled else None,
            created_at=now,
            updated_at=now,
        )
        session.add_all([search, revision])
        session.flush()
        return search, revision

    def patch(
        self,
        session: Session,
        search_id: UUID,
        *,
        name: str | None,
        preset_id: str | None,
        window_mode: WindowMode | None,
        filters: dict[str, Any] | None,
        enabled: bool | None,
        mode: SearchMode,
    ) -> tuple[SavedSearch, SavedSearchVersion]:
        search = session.execute(
            select(SavedSearch).where(SavedSearch.id == search_id).with_for_update()
        ).scalar_one_or_none()
        if search is None:
            raise SavedSearchNotFoundError("A busca salva não existe.")
        current = _current_revision(session, search)
        now = _database_now(session)

        next_name = _name(name) if name is not None else current.name
        next_preset_id = preset_id if preset_id is not None else current.preset_id
        next_window_mode = window_mode or cast(WindowMode, current.window_mode)
        next_filters = filters if filters is not None else dict(current.filters)
        content_changed = (
            next_name != current.name
            or next_preset_id != current.preset_id
            or next_window_mode != current.window_mode
            or _normalized_filter_dict(next_filters) != _normalized_filter_dict(current.filters)
        )
        if content_changed:
            revision = _make_revision(
                saved_search_id=search.id,
                version=search.current_version + 1,
                name=next_name,
                preset_id=next_preset_id,
                window_mode=next_window_mode,
                filters=next_filters,
                mode=mode,
                reference_time=now,
            )
            session.add(revision)
            search.name = next_name
            search.current_version = revision.version
        else:
            revision = current

        if enabled is not None and enabled != search.enabled:
            search.enabled = enabled
            search.next_run_at = next_daily_occurrence(now) if enabled else None
            if not enabled:
                session.execute(
                    update(ScheduleDispatch)
                    .where(
                        ScheduleDispatch.saved_search_id == search.id,
                        ScheduleDispatch.status == "pending",
                    )
                    .values(status="cancelled", reason="saved_search_disabled", updated_at=now)
                )
        search.updated_at = now
        session.flush()
        return search, revision

    def manual_run(
        self,
        session: Session,
        search_id: UUID,
        *,
        mode: SearchMode,
        source: str,
    ) -> EnqueueResult:
        search = session.execute(
            select(SavedSearch).where(SavedSearch.id == search_id).with_for_update()
        ).scalar_one_or_none()
        if search is None:
            raise SavedSearchNotFoundError("A busca salva não existe.")
        revision = _current_revision(session, search)
        now = _database_now(session)
        request = request_for_revision(
            revision,
            search_id=search.id,
            mode=mode,
            source=source,
            reference_time=now,
        )
        return self.jobs.enqueue_in_session(session, request)

    @staticmethod
    def current_revision(session: Session, search: SavedSearch) -> SavedSearchVersion:
        return _current_revision(session, search)


def request_for_revision(
    revision: SavedSearchVersion,
    *,
    search_id: UUID,
    mode: SearchMode,
    source: str,
    reference_time: datetime,
) -> EnqueueRequest:
    """Resolve a pinned preset revision into one bounded collection request."""

    if mode == "real":
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "A execução DataJud permanece desabilitada enquanto S1/S2 não forem aprovados.",
            validation_code="SOURCE_UNAVAILABLE",
        )
    filters = _normalized_filter_dict(revision.filters)
    filed_from = _optional_date(filters["filed_from"])
    filed_through = _optional_date(filters["filed_through"])
    if revision.window_mode == "fixed":
        if filed_from is None or filed_through is None:
            raise SourceError(SourceErrorCode.CONTRACT, "A janela fixa não possui datas completas.")
    elif filed_from is not None or filed_through is not None:
        raise SourceError(
            SourceErrorCode.CONTRACT,
            "A janela relativa contém datas fixas inesperadas.",
        )

    compiled = compile_preset(
        revision.preset_id,
        environment=mode,
        reference_time=reference_time,
        filed_from=filed_from,
        filed_through_inclusive=filed_through,
    )
    if compiled.item.version != revision.preset_version:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O preset salvo mudou de versão; esta busca precisa ser revisada antes de executar.",
            validation_code="SAVED_PRESET_VERSION_CHANGED",
        )
    stored_codes = revision.catalog_snapshot.get("effective_codes")
    current_codes = compiled.snapshot().get("effective_codes")
    if stored_codes != current_codes:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "Os filtros do preset salvo mudaram; esta busca precisa ser revisada "
            "antes de executar.",
            validation_code="SAVED_PRESET_FILTERS_CHANGED",
        )

    # Preserve the revision's rationale and evidence. Only the resolved dates move
    # for rolling windows, and build_collection_request validates this snapshot
    # against the compiled allowlisted query before enqueueing it.
    catalog_snapshot = deepcopy(revision.catalog_snapshot)
    fresh_snapshot = compiled.snapshot()
    catalog_snapshot["window_template"] = fresh_snapshot["window_template"]
    catalog_snapshot["resolved_interval"] = fresh_snapshot["resolved_interval"]
    request = build_collection_request(
        mode=mode,
        source=source,
        job_type="discovery",
        query=compiled.query,
        catalog_snapshot=catalog_snapshot,
        page_size=int(filters["page_size"]),
        hit_budget=int(filters["hit_budget"]),
    )
    return EnqueueRequest(
        mode=request.mode,
        job_type=request.job_type,
        source=request.source,
        tribunal=request.tribunal,
        resolved_query=request.resolved_query,
        sort=request.sort,
        parameters={
            **request.parameters,
            "saved_search_id": str(search_id),
            "saved_search_version": revision.version,
        },
    )


def availability_reasons(
    revision: SavedSearchVersion,
    *,
    search_id: UUID,
    mode: SearchMode,
    source: str,
    reference_time: datetime,
) -> list[str]:
    """Explain why a pinned search can or cannot create a collection now."""

    try:
        request_for_revision(
            revision,
            search_id=search_id,
            mode=mode,
            source=source,
            reference_time=reference_time,
        )
    except SourceError as error:
        return [str(error)]
    return []


def _make_revision(
    *,
    saved_search_id: UUID,
    version: int,
    name: str,
    preset_id: str,
    window_mode: WindowMode,
    filters: dict[str, Any],
    mode: SearchMode,
    reference_time: datetime,
) -> SavedSearchVersion:
    normalized_filters = _normalized_filter_dict(filters)
    filed_from = _optional_date(normalized_filters["filed_from"])
    filed_through = _optional_date(normalized_filters["filed_through"])
    if (window_mode == "fixed") != (filed_from is not None and filed_through is not None):
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "Janela fixa exige início e fim; janela rolling_12_months não aceita datas fixas.",
        )
    compile_mode: SearchMode = "demo" if mode == "real" else mode
    compiled = compile_preset(
        preset_id,
        environment=compile_mode,
        reference_time=reference_time,
        filed_from=filed_from,
        filed_through_inclusive=filed_through,
    )
    return SavedSearchVersion(
        id=uuid4(),
        saved_search_id=saved_search_id,
        version=version,
        name=name,
        preset_id=preset_id,
        preset_version=compiled.item.version,
        window_mode=window_mode,
        filters=normalized_filters,
        catalog_snapshot=compiled.snapshot(),
        created_at=reference_time,
    )


def _normalized_filter_dict(value: dict[str, Any]) -> dict[str, Any]:
    filed_from = value.get("filed_from")
    filed_through = value.get("filed_through")
    if isinstance(filed_from, date):
        filed_from = filed_from.isoformat()
    if isinstance(filed_through, date):
        filed_through = filed_through.isoformat()
    if filed_from is not None and not isinstance(filed_from, str):
        raise SourceError(SourceErrorCode.VALIDATION, "A data inicial salva é inválida.")
    if filed_through is not None and not isinstance(filed_through, str):
        raise SourceError(SourceErrorCode.VALIDATION, "A data final salva é inválida.")
    return {
        "filed_from": filed_from,
        "filed_through": filed_through,
        "page_size": int(value.get("page_size", 100)),
        "hit_budget": int(value.get("hit_budget", 2_000)),
    }


def _optional_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise SourceError(SourceErrorCode.VALIDATION, "A data salva é inválida.")


def _current_revision(session: Session, search: SavedSearch) -> SavedSearchVersion:
    revision = session.execute(
        select(SavedSearchVersion).where(
            SavedSearchVersion.saved_search_id == search.id,
            SavedSearchVersion.version == search.current_version,
        )
    ).scalar_one_or_none()
    if revision is None:  # pragma: no cover - created atomically by this service
        raise RuntimeError("A revisão atual da busca salva não foi encontrada.")
    return revision


def _database_now(session: Session) -> datetime:
    now = session.scalar(select(func.now()))
    if now is None:  # pragma: no cover - PostgreSQL always returns transaction time
        raise RuntimeError("O banco não retornou o horário atual.")
    return now


def _name(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 160:
        raise SourceError(SourceErrorCode.VALIDATION, "O nome da busca salva é inválido.")
    return normalized
