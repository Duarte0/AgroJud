"""Leased, checkpointed pagination for discovery and exact-number refresh jobs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

from agrojud.config import Settings
from agrojud.domain.canonical_json import JSONValue
from agrojud.services.job_worker import JobExecutionContext, JobOutcome
from agrojud.services.jobs import (
    EnqueueRequest,
    JobCancellationRequestedError,
    JobLease,
    LeaseLostError,
)
from agrojud.sources.contracts import (
    Cursor,
    JSONScalar,
    SortTerm,
    SourceAdapter,
    SourceError,
    SourceErrorCode,
    SourcePage,
    SourceQuery,
    build_query_by_case_number,
)
from agrojud.sources.factory import SourceKind, build_source_adapter

INITIAL_HIT_BUDGET = 2_000
DEFAULT_PAGE_SIZE = 100
MAX_CONTINUATION_BUDGET = 2_000
# SPEC-003 has not validated remote sorting or pagination for TJGO.
DATAJUD_PAGINATION_APPROVED = False

type SourceAdapterFactory = Callable[[JobLease], SourceAdapter]


@dataclass(frozen=True, slots=True)
class LimitContinuationPlan:
    cursor: Cursor
    checkpoint_revision: int
    coverage: dict[str, JSONValue]


def build_collection_request(
    *,
    mode: str,
    source: str,
    job_type: str,
    query: SourceQuery,
    page_size: int = DEFAULT_PAGE_SIZE,
    hit_budget: int = INITIAL_HIT_BUDGET,
) -> EnqueueRequest:
    """Freeze an allowlisted source query and the bounded first-cycle budget."""

    if mode not in ("demo", "real") or source not in ("synthetic", "datajud"):
        raise SourceError(SourceErrorCode.VALIDATION, "O modo ou a fonte da coleta é inválido.")
    if (mode == "demo") != (source == "synthetic"):
        raise SourceError(
            SourceErrorCode.VALIDATION, "O modo e a fonte da coleta não correspondem."
        )
    if job_type not in ("discovery", "refresh_number"):
        raise SourceError(SourceErrorCode.VALIDATION, "O tipo de coleta é inválido.")
    if (job_type == "refresh_number") != (query.process_number is not None):
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "Atualização por número exige consulta CNJ exata; descoberta exige outros filtros.",
        )
    if job_type == "refresh_number" and query != build_query_by_case_number(
        query.process_number or ""
    ):
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "Atualização por número exige a consulta CNJ exata sem filtros adicionais.",
        )
    _validate_limits(page_size, hit_budget)
    return EnqueueRequest(
        mode=cast(Any, mode),
        job_type=cast(Any, job_type),
        source=source,
        tribunal=query.tribunal,
        resolved_query=source_query_snapshot(query),
        sort=sort_snapshot(query.sort),
        parameters={"page_size": page_size, "hit_budget": hit_budget},
    )


def source_query_snapshot(query: SourceQuery) -> dict[str, JSONValue]:
    """Serialize only the source contract fields into immutable job JSON."""

    return {
        "tribunal": query.tribunal,
        "filed_from": query.filed_from.isoformat() if query.filed_from is not None else None,
        "filed_to": query.filed_to.isoformat() if query.filed_to is not None else None,
        "class_codes": list(query.class_codes),
        "subject_codes": list(query.subject_codes),
        "court_unit_code": query.court_unit_code,
        "preset_id": query.preset_id,
        "preset_version": query.preset_version,
        "process_number": query.process_number,
    }


def sort_snapshot(sort: Sequence[SortTerm]) -> list[JSONValue]:
    return [{"field_name": term.field_name, "order": term.order} for term in sort]


def source_query_from_snapshot(
    resolved_query: object,
    sort_value: object,
) -> SourceQuery:
    """Rebuild and revalidate the frozen query before any source request."""

    if not isinstance(resolved_query, Mapping):
        raise SourceError(SourceErrorCode.CONTRACT, "O snapshot da consulta é inválido.")
    allowed = {
        "tribunal",
        "filed_from",
        "filed_to",
        "class_codes",
        "subject_codes",
        "court_unit_code",
        "preset_id",
        "preset_version",
        "process_number",
    }
    if set(resolved_query) - allowed:
        raise SourceError(
            SourceErrorCode.CONTRACT, "O snapshot contém campos de consulta não permitidos."
        )

    sort = _sort_from_snapshot(sort_value)
    filed_from = _date_field(resolved_query.get("filed_from"), "filed_from")
    filed_to = _date_field(resolved_query.get("filed_to"), "filed_to")
    return SourceQuery(
        tribunal=_string_field(resolved_query.get("tribunal", "TJGO"), "tribunal"),
        filed_from=filed_from,
        filed_to=filed_to,
        class_codes=_code_list(resolved_query.get("class_codes", []), "class_codes"),
        subject_codes=_code_list(resolved_query.get("subject_codes", []), "subject_codes"),
        court_unit_code=_optional_positive_int(
            resolved_query.get("court_unit_code"), "court_unit_code"
        ),
        preset_id=_optional_string(resolved_query.get("preset_id"), "preset_id"),
        preset_version=_optional_string(resolved_query.get("preset_version"), "preset_version"),
        process_number=_optional_string(resolved_query.get("process_number"), "process_number"),
        sort=sort,
    )


def plan_limit_continuation(
    *,
    status: str,
    reason: str | None,
    cursor: object,
    checkpoint_revision: int,
    coverage: Mapping[str, Any] | None,
    additional_budget: int,
) -> LimitContinuationPlan:
    """Plan an internal limit continuation without changing checkpoint or counters.

    SPEC-009 owns the command and job lifecycle transition. This helper defines
    the bounded continuation contract that its command will persist atomically.
    """

    if status != "partial" or reason != "limit":
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "Só uma coleta parcial encerrada por limite pode continuar.",
        )
    if not _valid_cursor(cursor):
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "A coleta não possui cursor utilizável para continuação.",
        )
    if (
        isinstance(checkpoint_revision, bool)
        or not isinstance(checkpoint_revision, int)
        or checkpoint_revision < 0
    ):
        raise SourceError(SourceErrorCode.CONTRACT, "A revisão do checkpoint da coleta é inválida.")
    if (
        isinstance(additional_budget, bool)
        or not isinstance(additional_budget, int)
        or not 1 <= additional_budget <= MAX_CONTINUATION_BUDGET
    ):
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O orçamento adicional deve estar entre 1 e 2.000 hits.",
        )
    if coverage is None:
        raise SourceError(SourceErrorCode.CONTRACT, "A cobertura da coleta está ausente.")
    updated = dict(coverage)
    if updated.get("query_status") != "limit":
        raise SourceError(SourceErrorCode.CONTRACT, "A cobertura não registra limite atingido.")
    if _int_field(updated.get("checkpoint_revision"), "checkpoint_revision") != checkpoint_revision:
        raise SourceError(SourceErrorCode.CONTRACT, "A cobertura diverge da revisão do checkpoint.")
    current_budget = _int_field(updated.get("budget_limit"), "budget_limit", minimum=1)
    if _int_field(updated.get("hits_confirmed"), "hits_confirmed") < current_budget:
        raise SourceError(SourceErrorCode.CONTRACT, "O limite ainda não foi confirmado pelos hits.")
    updated["budget_limit"] = current_budget + additional_budget
    updated["query_status"] = "running"
    updated["continuations"] = _int_field(updated.get("continuations", 0), "continuations") + 1
    return LimitContinuationPlan(
        cursor=cast(Cursor, tuple(cast(Sequence[JSONScalar], cursor))),
        checkpoint_revision=checkpoint_revision,
        coverage=cast(dict[str, JSONValue], updated),
    )


class CollectionJobHandler:
    """Execute every page for one claimed discovery or refresh_number job."""

    def __init__(self, source_factory: SourceAdapterFactory) -> None:
        self.source_factory = source_factory

    def __call__(self, lease: JobLease, context: JobExecutionContext) -> JobOutcome:
        coverage = self._read_coverage(context, lease)
        if lease.mode == "real" and not DATAJUD_PAGINATION_APPROVED:
            blocked = dict(coverage)
            blocked["query_status"] = "blocked"
            blocked["has_persisted_data"] = bool(blocked.get("has_persisted_data", False))
            return JobOutcome(
                status="failed",
                coverage=cast(dict[str, JSONValue], blocked),
                reason="capability_not_approved",
                error_code="DATAJUD_PAGINATION_NOT_APPROVED",
                error_summary="A paginação TJGO aguarda validação de S2 na SPEC-003.",
            )

        try:
            query = source_query_from_snapshot(
                lease.parameters_snapshot.get("query"),
                lease.parameters_snapshot.get("sort"),
            )
            self._validate_job_query(lease, query)
            page_size, initial_budget = _limits_from_snapshot(lease.parameters_snapshot)
            budget_limit = _int_field(
                coverage.get("budget_limit", initial_budget), "budget_limit", minimum=1
            )
            if budget_limit > initial_budget + MAX_CONTINUATION_BUDGET * _int_field(
                coverage.get("continuations", 0), "continuations"
            ):
                raise SourceError(SourceErrorCode.CONTRACT, "O orçamento continuado é inválido.")
            checkpoint = context.jobs.inspect(lease.job_id).checkpoint

            if coverage.get("query_status") == "exhausted":
                return self._terminal_from_exhaustion(coverage)

            adapter = self.source_factory(lease)
            cursor = _cursor_from_json(checkpoint.cursor)
            revision = checkpoint.revision
            while True:
                context.assert_active()
                hits_confirmed = _int_field(coverage.get("hits_confirmed", 0), "hits_confirmed")
                remaining = budget_limit - hits_confirmed
                if remaining <= 0:
                    return self._limit_outcome(coverage)

                request_size = min(page_size, remaining)
                attempt_number = context.record_http_attempt(
                    lease,
                    expected_revision=revision,
                    budget_limit=budget_limit,
                )
                coverage["http_attempts"] = attempt_number
                coverage["current_page"] = checkpoint.next_page
                coverage["checkpoint_revision"] = revision
                page = self._fetch(adapter, lease, query, cursor, request_size)
                context.assert_active()
                self._validate_page(query, cursor, request_size, page)
                committed = context.commit_page(
                    lease,
                    expected_revision=revision,
                    budget_limit=budget_limit,
                    source=lease.source,  # type: ignore[arg-type]
                    page=page,
                )
                coverage = cast(dict[str, Any], committed.coverage)
                checkpoint = committed.checkpoint
                cursor = _cursor_from_json(checkpoint.cursor)
                revision = checkpoint.revision

                if page.is_empty:
                    return self._terminal_from_exhaustion(coverage)
                if _int_field(coverage.get("hits_confirmed", 0), "hits_confirmed") >= budget_limit:
                    return self._limit_outcome(coverage)
        except JobCancellationRequestedError:
            return JobOutcome(status="cancelled", coverage=self._coverage_json(coverage))
        except LeaseLostError:
            raise
        except SourceError as error:
            return self._failure_outcome(coverage, error.code.value, error.message)
        except Exception:
            if context.ownership_lost.is_set():
                raise LeaseLostError("A posse do job foi perdida durante a coleta.") from None
            if context.cancellation_requested.is_set():
                return JobOutcome(status="cancelled", coverage=self._coverage_json(coverage))
            return self._failure_outcome(
                coverage,
                "persistence_error",
                "Falha ao persistir a página da coleta.",
            )

    @staticmethod
    def _validate_job_query(lease: JobLease, query: SourceQuery) -> None:
        if query.tribunal != lease.tribunal:
            raise SourceError(SourceErrorCode.CONTRACT, "O tribunal diverge do job persistido.")
        if lease.job_type == "refresh_number" and query.process_number is None:
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "Atualização por número não contém um número CNJ exato.",
            )
        if lease.job_type == "refresh_number" and query != build_query_by_case_number(
            query.process_number or ""
        ):
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "Atualização por número contém filtros além do CNJ exato.",
            )
        if lease.job_type == "discovery" and query.process_number is not None:
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "Descoberta não pode substituir seus filtros por um número exato.",
            )
        if lease.mode == "demo" and lease.source != "synthetic":
            raise SourceError(SourceErrorCode.VALIDATION, "O modo demo exige fonte sintética.")
        if lease.mode == "real" and lease.source != "datajud":
            raise SourceError(SourceErrorCode.VALIDATION, "O modo real exige fonte DataJud.")

    @staticmethod
    def _fetch(
        adapter: SourceAdapter,
        lease: JobLease,
        query: SourceQuery,
        cursor: Cursor | None,
        page_size: int,
    ) -> SourcePage:
        if lease.job_type == "refresh_number":
            if query.process_number is None:  # pragma: no cover - validated above
                raise SourceError(
                    SourceErrorCode.CONTRACT, "O número CNJ da consulta está ausente."
                )
            return adapter.fetch_by_case_number(query.process_number, cursor, page_size)
        return adapter.fetch_page(query, cursor, page_size)

    @staticmethod
    def _validate_page(
        query: SourceQuery,
        previous_cursor: Cursor | None,
        requested_size: int,
        page: SourcePage,
    ) -> None:
        if len(page.hits) > requested_size:
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "A fonte retornou mais hits que o tamanho solicitado.",
            )
        if page.responded_at.tzinfo is None or page.responded_at.utcoffset() is None:
            raise SourceError(SourceErrorCode.CONTRACT, "A página não contém horário com fuso.")
        if not page.hits:
            if page.cursor_final is not None:
                raise SourceError(
                    SourceErrorCode.CONTRACT,
                    "Uma página vazia retornou cursor final.",
                )
            return

        expected_length = len(query.sort)
        previous = previous_cursor
        for hit in page.hits:
            if len(hit.sort_values) != expected_length:
                raise SourceError(
                    SourceErrorCode.CONTRACT,
                    "A ordenação retornada não corresponde à consulta aprovada.",
                )
            if previous is not None and not _cursor_advances(previous, hit.sort_values, query.sort):
                raise SourceError(
                    SourceErrorCode.CONTRACT,
                    "O cursor da fonte repetiu ou recuou na ordenação aprovada.",
                )
            previous = hit.sort_values
        if page.cursor_final != page.hits[-1].sort_values:
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "O cursor final não corresponde integralmente ao sort do último hit.",
            )

    @staticmethod
    def _terminal_from_exhaustion(coverage: Mapping[str, Any]) -> JobOutcome:
        final = dict(coverage)
        final["query_status"] = "exhausted"
        has_rejections = bool(
            _int_field(final.get("rejected_hits", 0), "rejected_hits")
            or _int_field(final.get("quarantine_records", 0), "quarantine_records")
        )
        if has_rejections:
            final["coverage"] = "partial"
            return JobOutcome(
                status="partial",
                coverage=CollectionJobHandler._coverage_json(final),
                reason="rejections",
            )
        final["coverage"] = "complete"
        return JobOutcome(
            status="completed",
            coverage=CollectionJobHandler._coverage_json(final),
        )

    @staticmethod
    def _limit_outcome(coverage: Mapping[str, Any]) -> JobOutcome:
        final = dict(coverage)
        final["query_status"] = "limit"
        final["coverage"] = "partial"
        final["has_persisted_data"] = bool(final.get("has_persisted_data", False))
        return JobOutcome(
            status="partial",
            coverage=CollectionJobHandler._coverage_json(final),
            reason="limit",
        )

    @staticmethod
    def _failure_outcome(
        coverage: Mapping[str, Any],
        error_code: str,
        summary: str,
    ) -> JobOutcome:
        final = dict(coverage)
        has_data = bool(final.get("has_persisted_data", False))
        final["has_persisted_data"] = has_data
        final["query_status"] = "failed"
        if has_data:
            final["coverage"] = "partial"
        return JobOutcome(
            status="failed",
            coverage=CollectionJobHandler._coverage_json(final),
            reason="source_error" if error_code != "persistence_error" else "persistence_error",
            error_code=error_code,
            error_summary=summary,
        )

    @staticmethod
    def _coverage_json(value: Mapping[str, Any]) -> dict[str, JSONValue]:
        return cast(dict[str, JSONValue], dict(value))

    @staticmethod
    def _read_coverage(context: JobExecutionContext, lease: JobLease) -> dict[str, Any]:
        inspection = context.jobs.inspect(lease.job_id)
        if inspection.coverage is None:
            return {
                "pages_confirmed": 0,
                "hits_confirmed": 0,
                "valid_hits": 0,
                "rejected_hits": 0,
                "new": 0,
                "updated": 0,
                "unchanged": 0,
                "quarantine_records": 0,
                "http_attempts": 0,
                "has_persisted_data": False,
            }
        if not isinstance(inspection.coverage, dict):
            raise SourceError(SourceErrorCode.CONTRACT, "A cobertura do job é inválida.")
        return dict(inspection.coverage)


def build_collection_job_handler(settings: Settings) -> CollectionJobHandler:
    """Create production source selection while keeping real pagination gated."""

    def source_factory(lease: JobLease) -> SourceAdapter:
        return build_source_adapter(settings, SourceKind(lease.source))

    return CollectionJobHandler(source_factory)


def _validate_limits(page_size: int, hit_budget: int) -> None:
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise SourceError(
            SourceErrorCode.VALIDATION, "O tamanho da página deve estar entre 1 e 100."
        )
    if (
        isinstance(hit_budget, bool)
        or not isinstance(hit_budget, int)
        or not 1 <= hit_budget <= INITIAL_HIT_BUDGET
    ):
        raise SourceError(
            SourceErrorCode.VALIDATION, "O orçamento inicial deve estar entre 1 e 2.000 hits."
        )


def _limits_from_snapshot(snapshot: Mapping[str, Any]) -> tuple[int, int]:
    parameters = snapshot.get("parameters")
    if not isinstance(parameters, Mapping):
        raise SourceError(SourceErrorCode.CONTRACT, "Os parâmetros do job são inválidos.")
    page_size = parameters.get("page_size", DEFAULT_PAGE_SIZE)
    hit_budget = parameters.get("hit_budget", parameters.get("budget", INITIAL_HIT_BUDGET))
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise SourceError(SourceErrorCode.CONTRACT, "O tamanho de página persistido é inválido.")
    if (
        isinstance(hit_budget, bool)
        or not isinstance(hit_budget, int)
        or not 1 <= hit_budget <= INITIAL_HIT_BUDGET
    ):
        raise SourceError(SourceErrorCode.CONTRACT, "O orçamento inicial persistido é inválido.")
    return page_size, hit_budget


def _sort_from_snapshot(value: object) -> tuple[SortTerm, ...]:
    if not isinstance(value, list) or not value:
        raise SourceError(SourceErrorCode.CONTRACT, "A ordenação congelada do job é inválida.")
    terms: list[SortTerm] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"field_name", "order"}:
            raise SourceError(
                SourceErrorCode.CONTRACT, "Um termo de ordenação congelado é inválido."
            )
        field_name = item["field_name"]
        order = item["order"]
        if not isinstance(field_name, str) or order not in ("asc", "desc"):
            raise SourceError(
                SourceErrorCode.CONTRACT, "Um termo de ordenação congelado é inválido."
            )
        terms.append(SortTerm(field_name=field_name, order=cast(Any, order)))
    return tuple(terms)


def _date_field(value: object, name: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceError(SourceErrorCode.CONTRACT, f"O campo {name} do snapshot não é data.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SourceError(
            SourceErrorCode.CONTRACT, f"O campo {name} do snapshot é inválido."
        ) from None


def _code_list(value: object, name: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise SourceError(SourceErrorCode.CONTRACT, f"A lista {name} do snapshot é inválida.")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise SourceError(SourceErrorCode.CONTRACT, f"A lista {name} do snapshot é inválida.")
    return tuple(value)


def _string_field(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise SourceError(SourceErrorCode.CONTRACT, f"O campo {name} do snapshot é inválido.")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _string_field(value, name)


def _optional_positive_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _int_field(value, name, minimum=1)


def _int_field(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SourceError(SourceErrorCode.CONTRACT, f"O campo {name} do snapshot é inválido.")
    return value


def _cursor_from_json(value: object) -> Cursor | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not _valid_cursor(value):
        raise SourceError(SourceErrorCode.CONTRACT, "O cursor persistido do job é inválido.")
    return cast(Cursor, tuple(cast(Sequence[JSONScalar], value)))


def _valid_cursor(value: object) -> bool:
    if not isinstance(value, (list, tuple)) or not value:
        return False
    return all(
        item is None
        or isinstance(item, (str, int, float, bool))
        and not (
            isinstance(item, float) and (item != item or item in (float("inf"), float("-inf")))
        )
        for item in value
    )


def _cursor_advances(previous: Cursor, current: Cursor, sort: Sequence[SortTerm]) -> bool:
    if len(previous) != len(current) or len(current) != len(sort):
        return False
    for left, right, term in zip(previous, current, sort, strict=True):
        if type(left) is type(right) and left == right:
            continue
        if left is None or right is None or type(left) is not type(right):
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "Os valores de ordenação mudaram de tipo ou não são comparáveis.",
            )
        try:
            left_value = cast(Any, left)
            right_value = cast(Any, right)
            advances = right_value > left_value if term.order == "asc" else right_value < left_value
            retreats = right_value < left_value if term.order == "asc" else right_value > left_value
        except TypeError:
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "Os valores de ordenação não são comparáveis.",
            ) from None
        if advances:
            return True
        if retreats:
            return False
        # Distinct values that cannot be ordered violate the approved cursor contract.
        raise SourceError(
            SourceErrorCode.CONTRACT,
            "Os valores de ordenação não permitem provar avanço do cursor.",
        )
    return False
