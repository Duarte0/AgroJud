"""Bounded, sanitized TJGO contract and pagination validation probe."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import cast

from pydantic import ValidationError

from agrojud.config import Settings, get_settings
from agrojud.sources.contracts import (
    Cursor,
    SourceError,
    SourceErrorCode,
    SourceHit,
    SourcePage,
    SourceQuery,
    build_datajud_payload,
    build_query_by_case_number,
)
from agrojud.sources.datajud import (
    DATAJUD_TJGO_ENDPOINT,
    DataJudProbeResponse,
    DataJudSourceAdapter,
)

MAX_REQUESTS = 6
PAGE_SIZE = 100
_CNJ = re.compile(r"^\d{20}$")
_SORT_RESPONSE_MISMATCH = "A resposta do DataJud não corresponde à ordenação solicitada."
_FIELD_PATHS = (
    "id",
    "tribunal",
    "numeroProcesso",
    "dataAjuizamento",
    "grau",
    "classe",
    "assuntos",
    "orgaoJulgador",
    "movimentos",
    "@timestamp",
)
_STOP_CODES = {
    SourceErrorCode.AUTHENTICATION,
    SourceErrorCode.AUTHORIZATION,
    SourceErrorCode.CONTRACT,
    SourceErrorCode.NETWORK,
    SourceErrorCode.RATE_LIMIT,
    SourceErrorCode.SOURCE_UNAVAILABLE,
}


class CapabilityState(StrEnum):
    """Evidence state for one independently assessed remote capability."""

    VALIDATED = "VALIDATED"
    INCOMPATIBLE = "INCOMPATIBLE"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class ProbeRequestResult:
    """A safe summary of one page returned by the DataJud adapter."""

    name: str
    page: SourcePage
    status_code: int
    duration_ms: int
    request_payload: dict[str, object]
    sort_fields: tuple[str, ...]


class TJGOValidationProbe:
    """Run at most six requests without writing or retaining raw hits in the report."""

    def __init__(
        self,
        adapter: DataJudSourceAdapter,
        *,
        filed_from: date,
        filed_to: date,
    ) -> None:
        if filed_from >= filed_to:
            raise ValueError("A data inicial precisa ser anterior à data final exclusiva.")
        self._adapter = adapter
        self._query = SourceQuery(filed_from=filed_from, filed_to=filed_to)
        self._filed_from = filed_from
        self._filed_to = filed_to
        self._requests: list[dict[str, object]] = []
        self._repeat_comparison: dict[str, object] | None = None
        self._stopped = False

    def run(self, *, started_at: datetime | None = None) -> dict[str, object]:
        """Run the bounded probe and return a report containing no raw response data."""

        started = started_at or datetime.now(UTC)
        run_started = perf_counter()
        capabilities = _initial_capabilities("Nenhuma resposta externa foi observada.")

        first = self._request("recorte_timestamp_pagina_1", self._query)
        if first is None:
            self._assess_initial_failure(capabilities)
        else:
            self._assess_envelope(first.page, capabilities)
            self._assess_essential_fields(first.page, capabilities)
            self._assess_filters(first.page, capabilities)
            self._assess_identity(first.page, capabilities)
            self._assess_timestamp_sort(first, capabilities)

            observed_number = _first_cnj(first.page.hits)
            exact: ProbeRequestResult | None = None
            if observed_number is not None and not self._stopped:
                exact_query = build_query_by_case_number(observed_number)
                exact = self._request("consulta_cnj_observado", exact_query)
                self._assess_case_number_query(exact, observed_number, capabilities)
            elif observed_number is None:
                _set_capability(
                    capabilities,
                    "query_by_case_number",
                    CapabilityState.INCONCLUSIVE,
                    "A amostra não contém numeroProcesso utilizável para construir "
                    "uma busca exata.",
                )

            repeated: ProbeRequestResult | None = None
            second: ProbeRequestResult | None = None
            compound_first: ProbeRequestResult | None = None
            compound_second: ProbeRequestResult | None = None
            if not self._stopped:
                repeated = self._request("repeticao_recorte_timestamp_pagina_1", self._query)
            if repeated is not None:
                self._record_repeat_comparison(first.page, repeated.page)

            if first.page.hits and first.page.cursor_final is not None and not self._stopped:
                second = self._request(
                    "recorte_timestamp_pagina_2",
                    self._query,
                    cursor=first.page.cursor_final,
                )

            if first.page.hits and not self._stopped:
                compound_first = self._request(
                    "recorte_sort_composto_pagina_1",
                    self._query,
                    include_source_id_tiebreaker=True,
                )
                self._assess_compound_sort(compound_first, capabilities)
            if (
                compound_first is not None
                and compound_first.page.hits
                and compound_first.page.cursor_final is not None
                and not self._stopped
            ):
                compound_second = self._request(
                    "recorte_sort_composto_pagina_2",
                    self._query,
                    cursor=compound_first.page.cursor_final,
                    include_source_id_tiebreaker=True,
                )

            self._assess_pagination(
                first,
                second,
                compound_first,
                compound_second,
                capabilities,
            )

        report_duration_ms = max(0, round((perf_counter() - run_started) * 1000))
        report: dict[str, object] = {
            "report_version": 1,
            "started_at": started.astimezone(UTC).isoformat(),
            "endpoint": DATAJUD_TJGO_ENDPOINT,
            "request_budget": {
                "max_requests": MAX_REQUESTS,
                "page_size": PAGE_SIZE,
                "automatic_retries": False,
                "writes_to_product_database": False,
            },
            "requests_made": len(self._requests),
            "duration_ms": report_duration_ms,
            "planned_recorte_query": _sanitize_payload(
                build_datajud_payload(self._query, None, PAGE_SIZE)
            ),
            "requests": self._requests,
            "repeat_comparison": self._repeat_comparison,
            "capabilities": capabilities,
            "conclusion": _report_conclusion(capabilities),
        }
        return report

    def _request(
        self,
        name: str,
        query: SourceQuery,
        *,
        cursor: Cursor | None = None,
        include_source_id_tiebreaker: bool = False,
    ) -> ProbeRequestResult | None:
        if self._stopped or len(self._requests) >= MAX_REQUESTS:
            return None

        request_payload = build_datajud_payload(query, cursor, PAGE_SIZE)
        sort_fields = tuple(
            next(iter(term)) for term in cast(list[dict[str, object]], request_payload["sort"])
        )
        if include_source_id_tiebreaker:
            request_payload["sort"] = [
                {"@timestamp": {"order": "asc"}},
                {"id.keyword": {"order": "asc"}},
            ]
            sort_fields = ("@timestamp", "id.keyword")

        safe_payload = cast(dict[str, object], _sanitize_payload(request_payload))
        request_started = perf_counter()
        try:
            response: DataJudProbeResponse = self._adapter.fetch_probe_page(
                query,
                cursor,
                PAGE_SIZE,
                include_source_id_tiebreaker=include_source_id_tiebreaker,
            )
        except SourceError as error:
            duration_ms = max(0, round((perf_counter() - request_started) * 1000))
            self._requests.append(
                {
                    "name": name,
                    "query": safe_payload,
                    "http_status": error.status_code,
                    "duration_ms": duration_ms,
                    "outcome": "ERROR",
                    "error_code": error.code.value,
                    "diagnostic": error.message,
                }
            )
            if error.code in _STOP_CODES:
                self._stopped = True
            return None

        duration_ms = max(0, round((perf_counter() - request_started) * 1000))
        record = ProbeRequestResult(
            name=name,
            page=response.page,
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_payload=safe_payload,
            sort_fields=sort_fields,
        )
        self._requests.append(_request_record(record))
        return record

    def _assess_envelope(
        self,
        page: SourcePage,
        capabilities: dict[str, dict[str, object]],
    ) -> None:
        _set_capability(
            capabilities,
            "envelope",
            CapabilityState.VALIDATED,
            "Resposta HTTP válida parseada como hits.total e hits.hits.",
            hit_count=len(page.hits),
            total_value=page.total_value,
            total_relation=page.total_relation,
        )

    def _assess_initial_failure(self, capabilities: dict[str, dict[str, object]]) -> None:
        if not self._requests:
            return
        failure = self._requests[-1]
        error_code = failure.get("error_code")
        status = failure.get("http_status")
        if error_code == SourceErrorCode.CONTRACT.value and status == 200:
            if failure.get("diagnostic") == _SORT_RESPONSE_MISMATCH:
                _set_capability(
                    capabilities,
                    "envelope",
                    CapabilityState.VALIDATED,
                    "O envelope foi parseado antes da incompatibilidade nos valores sort.",
                    http_status=200,
                )
                _set_capability(
                    capabilities,
                    "sort_timestamp",
                    CapabilityState.INCOMPATIBLE,
                    "A quantidade de valores sort recebida difere do sort solicitado.",
                    http_status=200,
                )
            else:
                _set_capability(
                    capabilities,
                    "envelope",
                    CapabilityState.INCOMPATIBLE,
                    "HTTP 200 não trouxe o envelope de resposta aceito pelo adaptador.",
                    http_status=200,
                    error_code=error_code,
                )
        elif error_code == SourceErrorCode.VALIDATION.value:
            _set_capability(
                capabilities,
                "filters",
                CapabilityState.INCOMPATIBLE,
                "O endpoint rejeitou o recorte de diagnóstico enviado.",
                http_status=status,
                error_code=error_code,
            )
        else:
            reason = cast(str, failure.get("diagnostic", "A consulta não retornou evidência."))
            for name in capabilities:
                _set_capability(
                    capabilities,
                    name,
                    CapabilityState.INCONCLUSIVE,
                    reason,
                    http_status=status,
                    error_code=error_code,
                )

    def _assess_essential_fields(
        self,
        page: SourcePage,
        capabilities: dict[str, dict[str, object]],
    ) -> None:
        if not page.hits:
            _set_capability(
                capabilities,
                "essential_fields",
                CapabilityState.INCONCLUSIVE,
                "A resposta não contém hits para observar os campos essenciais.",
            )
            return
        valid = all(_has_essential_identity_fields(hit) for hit in page.hits)
        _set_capability(
            capabilities,
            "essential_fields",
            CapabilityState.VALIDATED if valid else CapabilityState.INCOMPATIBLE,
            "Amostra observada; a conclusão se limita aos hits desta página.",
            sample_count=len(page.hits),
            fields=_field_shapes(page.hits),
        )

    def _assess_filters(
        self,
        page: SourcePage,
        capabilities: dict[str, dict[str, object]],
    ) -> None:
        if not page.hits:
            _set_capability(
                capabilities,
                "filters",
                CapabilityState.INCONCLUSIVE,
                "Recorte aceito sem hits; a semântica do filtro não pôde ser conferida na amostra.",
            )
            return
        dates: list[date] = []
        missing_date = False
        wrong_value = False
        for hit in page.hits:
            source = _source_mapping(hit)
            raw_date = source.get("dataAjuizamento") if source is not None else None
            parsed_date = _parse_source_date(raw_date)
            if parsed_date is None:
                missing_date = True
            else:
                dates.append(parsed_date)
                if not self._filed_from <= parsed_date < self._filed_to:
                    wrong_value = True
            if source is None or source.get("tribunal") != "TJGO":
                wrong_value = True
        if wrong_value:
            state = CapabilityState.INCOMPATIBLE
            reason = (
                "A amostra contém valor fora do tribunal TJGO ou do intervalo de "
                "ajuizamento solicitado."
            )
        elif missing_date:
            state = CapabilityState.INCONCLUSIVE
            reason = (
                "A resposta foi aceita, mas faltam datas parseáveis para conferir todo o recorte."
            )
        else:
            state = CapabilityState.VALIDATED
            reason = (
                "Tribunal e intervalo exclusivo conferidos em todos os hits da página observada."
            )
        _set_capability(
            capabilities,
            "filters",
            state,
            reason,
            sample_count=len(page.hits),
            date_min=min(dates).isoformat() if dates else None,
            date_max=max(dates).isoformat() if dates else None,
        )

    def _assess_identity(
        self,
        page: SourcePage,
        capabilities: dict[str, dict[str, object]],
    ) -> None:
        if not page.hits:
            _set_capability(
                capabilities,
                "source_identity",
                CapabilityState.INCONCLUSIVE,
                "A resposta não contém hits para comparar _id, _source.id e numeroProcesso.",
            )
            return
        matched = 0
        invalid = 0
        for hit in page.hits:
            source = _source_mapping(hit)
            source_id = source.get("id") if source is not None else None
            process_number = source.get("numeroProcesso") if source is not None else None
            if (
                isinstance(hit.source_id, str)
                and isinstance(source_id, str)
                and hit.source_id == source_id
                and isinstance(process_number, str)
                and _CNJ.fullmatch(process_number)
                and source is not None
                and source.get("tribunal") == "TJGO"
            ):
                matched += 1
            else:
                invalid += 1
        state = CapabilityState.VALIDATED if invalid == 0 else CapabilityState.INCOMPATIBLE
        _set_capability(
            capabilities,
            "source_identity",
            state,
            "Relação observada nesta amostra; uma leitura não demonstra estabilidade histórica.",
            sample_count=len(page.hits),
            matching_hits=matched,
            invalid_hits=invalid,
        )

    def _assess_timestamp_sort(
        self,
        result: ProbeRequestResult,
        capabilities: dict[str, dict[str, object]],
    ) -> None:
        if not result.page.hits:
            _set_capability(
                capabilities,
                "sort_timestamp",
                CapabilityState.INCONCLUSIVE,
                "O sort foi enviado, mas a página vazia não trouxe valores sort para conferência.",
                requested_fields=list(result.sort_fields),
            )
            return
        is_valid, ties, types = _sort_observation(result.page.hits, 1)
        state = CapabilityState.VALIDATED if is_valid else CapabilityState.INCOMPATIBLE
        _set_capability(
            capabilities,
            "sort_timestamp",
            state,
            "Valores sort observados e conferidos na ordem ascendente da amostra."
            if is_valid
            else "Os valores sort recebidos não correspondem ao campo único esperado.",
            requested_fields=list(result.sort_fields),
            received_values_per_hit=1,
            value_types=types,
            duplicate_primary_values=ties,
            sample_count=len(result.page.hits),
        )

    def _assess_case_number_query(
        self,
        result: ProbeRequestResult | None,
        observed_number: str,
        capabilities: dict[str, dict[str, object]],
    ) -> None:
        if result is None:
            failure = self._requests[-1] if self._requests else {}
            error_code = failure.get("error_code")
            if error_code == SourceErrorCode.VALIDATION.value:
                _set_capability(
                    capabilities,
                    "query_by_case_number",
                    CapabilityState.INCOMPATIBLE,
                    "O endpoint rejeitou a consulta pelo numeroProcesso observado.",
                    http_status=failure.get("http_status"),
                    queried_value="CNJ observado redigido",
                )
            elif not self._stopped:
                _set_capability(
                    capabilities,
                    "query_by_case_number",
                    CapabilityState.INCONCLUSIVE,
                    "A consulta exata não foi executada.",
                )
            return
        matching = 0
        different = 0
        for hit in result.page.hits:
            source = _source_mapping(hit)
            number = source.get("numeroProcesso") if source is not None else None
            if number == observed_number:
                matching += 1
            else:
                different += 1
        if matching > 0 and different == 0:
            state = CapabilityState.VALIDATED
            reason = (
                "A busca construída do número observado retornou somente esse número na amostra."
            )
        else:
            state = CapabilityState.INCOMPATIBLE
            reason = (
                "A busca exata aceita pela API não retornou somente o número usado "
                "para consultá-la."
            )
        _set_capability(
            capabilities,
            "query_by_case_number",
            state,
            reason,
            returned_hits=len(result.page.hits),
            matching_hits=matching,
            different_hits=different,
            queried_value="CNJ observado redigido",
        )

    def _assess_compound_sort(
        self,
        result: ProbeRequestResult | None,
        capabilities: dict[str, dict[str, object]],
    ) -> None:
        if result is None:
            error = self._requests[-1] if self._requests else {}
            is_rejected = error.get("error_code") == SourceErrorCode.VALIDATION.value or (
                error.get("error_code") == SourceErrorCode.CONTRACT.value
                and error.get("http_status") == 200
            )
            if is_rejected:
                _set_capability(
                    capabilities,
                    "compound_sort",
                    CapabilityState.INCOMPATIBLE,
                    "O endpoint rejeitou o sort composto candidato.",
                    requested_fields=["@timestamp", "id.keyword"],
                    http_status=error.get("http_status"),
                )
            else:
                _set_capability(
                    capabilities,
                    "compound_sort",
                    CapabilityState.INCONCLUSIVE,
                    "O sort composto não produziu resposta observável.",
                )
            return
        if not result.page.hits:
            _set_capability(
                capabilities,
                "compound_sort",
                CapabilityState.INCONCLUSIVE,
                "O endpoint aceitou a consulta, mas não retornou hits com valores sort.",
                requested_fields=list(result.sort_fields),
            )
            return
        valid, ties, types = _sort_observation(result.page.hits, 2)
        state = CapabilityState.VALIDATED if valid else CapabilityState.INCOMPATIBLE
        _set_capability(
            capabilities,
            "compound_sort",
            state,
            "Os dois valores sort por hit foram observados e ordenados na amostra."
            if valid
            else "A resposta não trouxe valores compatíveis com @timestamp e id.keyword.",
            requested_fields=list(result.sort_fields),
            received_values_per_hit=2,
            value_types=types,
            duplicate_sort_tuples=ties,
            sample_count=len(result.page.hits),
        )

    def _assess_pagination(
        self,
        timestamp_first: ProbeRequestResult,
        timestamp_second: ProbeRequestResult | None,
        compound_first: ProbeRequestResult | None,
        compound_second: ProbeRequestResult | None,
        capabilities: dict[str, dict[str, object]],
    ) -> None:
        if compound_first is not None and compound_second is not None:
            state, evidence = _evaluate_pagination(compound_first.page, compound_second.page, 2)
            evidence["sort_fields"] = ["@timestamp", "id.keyword"]
            if state is not CapabilityState.INCONCLUSIVE or not timestamp_first.page.hits:
                _set_capability(
                    capabilities,
                    "pagination",
                    state,
                    "Paginação avaliada em duas páginas sob sort composto candidato.",
                    **evidence,
                )
                return

        if timestamp_second is None:
            _set_capability(
                capabilities,
                "pagination",
                CapabilityState.INCONCLUSIVE,
                "Não foram observadas duas páginas não vazias com cursor para o sort @timestamp.",
                first_page_hits=len(timestamp_first.page.hits),
            )
            return
        state, evidence = _evaluate_pagination(timestamp_first.page, timestamp_second.page, 1)
        timestamp_ties = _primary_tie_count(timestamp_first.page.hits) + _primary_tie_count(
            timestamp_second.page.hits
        )
        if timestamp_first.page.cursor_final and timestamp_second.page.hits:
            if timestamp_first.page.cursor_final[0] == timestamp_second.page.hits[0].sort_values[0]:
                timestamp_ties += 1
        evidence["sort_fields"] = ["@timestamp"]
        evidence["timestamp_ties_observed"] = timestamp_ties
        if state is CapabilityState.VALIDATED and timestamp_ties:
            state = CapabilityState.INCONCLUSIVE
            reason = (
                "Duas páginas avançaram, mas houve empates em @timestamp e o "
                "desempate por id.keyword "
                "não foi demonstrado em duas páginas."
            )
        else:
            reason = "Paginação avaliada em duas páginas sob o sort documentado @timestamp."
        _set_capability(capabilities, "pagination", state, reason, **evidence)

    def _record_repeat_comparison(self, first: SourcePage, repeated: SourcePage) -> None:
        first_ids = _document_ids(first.hits)
        repeated_ids = _document_ids(repeated.hits)
        overlap = len(first_ids.intersection(repeated_ids))
        self._repeat_comparison = {
            "first_page_hits": len(first.hits),
            "repeated_page_hits": len(repeated.hits),
            "overlap_by_source_document_id": overlap,
            "same_hit_count": len(first.hits) == len(repeated.hits),
            "same_cursor": first.cursor_final == repeated.cursor_final,
            "interpretation": (
                "Comparação de duas leituras; não representa snapshot consistente da fonte."
            ),
        }


def missing_key_report(
    *,
    filed_from: date,
    filed_to: date,
    started_at: datetime | None = None,
    reason: str = "DATAJUD_API_KEY não configurada; nenhuma solicitação HTTP foi enviada.",
) -> dict[str, object]:
    """Build the required local failure report without initializing an HTTP client."""

    query = SourceQuery(filed_from=filed_from, filed_to=filed_to)
    capabilities = _initial_capabilities(reason)
    return {
        "report_version": 1,
        "started_at": (started_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "endpoint": DATAJUD_TJGO_ENDPOINT,
        "request_budget": {
            "max_requests": MAX_REQUESTS,
            "page_size": PAGE_SIZE,
            "automatic_retries": False,
            "writes_to_product_database": False,
        },
        "requests_made": 0,
        "duration_ms": 0,
        "planned_recorte_query": _sanitize_payload(build_datajud_payload(query, None, PAGE_SIZE)),
        "requests": [],
        "repeat_comparison": None,
        "capabilities": capabilities,
        "conclusion": "INCONCLUSIVE",
    }


def write_report(report: Mapping[str, object], output: Path) -> None:
    """Write a UTF-8 JSON report after ensuring its parent directory exists."""

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _initial_capabilities(reason: str) -> dict[str, dict[str, object]]:
    return {
        name: {
            "state": CapabilityState.INCONCLUSIVE.value,
            "evidence": {"reason": reason},
        }
        for name in (
            "envelope",
            "essential_fields",
            "query_by_case_number",
            "filters",
            "source_identity",
            "sort_timestamp",
            "compound_sort",
            "pagination",
        )
    }


def _set_capability(
    capabilities: dict[str, dict[str, object]],
    name: str,
    state: CapabilityState,
    reason: str,
    **evidence: object,
) -> None:
    capabilities[name] = {
        "state": state.value,
        "evidence": {"reason": reason, **evidence},
    }


def _request_record(result: ProbeRequestResult) -> dict[str, object]:
    hits = result.page.hits
    return {
        "name": result.name,
        "query": result.request_payload,
        "http_status": result.status_code,
        "duration_ms": result.duration_ms,
        "outcome": "SUCCESS",
        "response": {
            "hit_count": len(hits),
            "total_value": result.page.total_value,
            "total_relation": result.page.total_relation,
            "field_shapes": _field_shapes(hits),
            "sort_received": _sort_shape(hits, len(result.sort_fields)),
        },
    }


def _field_shapes(hits: tuple[SourceHit, ...]) -> dict[str, dict[str, object]]:
    shapes: dict[str, dict[str, object]] = {}
    sources = [_source_mapping(hit) for hit in hits]
    for path in _FIELD_PATHS:
        values = [_get_source_path(source, path) for source in sources]
        present = [value for value in values if value is not _MISSING]
        shapes[path] = {
            "present_count": len(present),
            "sample_count": len(hits),
            "types": sorted({_json_type(value) for value in present}),
        }
    return shapes


def _sort_shape(hits: tuple[SourceHit, ...], field_count: int) -> dict[str, object]:
    types_by_position: list[list[str]] = []
    for index in range(field_count):
        types_by_position.append(
            sorted(
                {_json_type(hit.sort_values[index]) for hit in hits if len(hit.sort_values) > index}
            )
        )
    valid, ties, _ = _sort_observation(hits, field_count)
    return {
        "fields": ["@timestamp"] if field_count == 1 else ["@timestamp", "id.keyword"],
        "values_per_hit": sorted({len(hit.sort_values) for hit in hits}),
        "value_types_by_position": types_by_position,
        "ordered_ascending_in_sample": valid,
        "duplicate_primary_values": ties,
    }


def _sort_observation(
    hits: tuple[SourceHit, ...], field_count: int
) -> tuple[bool, int, list[list[str]]]:
    if not hits or any(len(hit.sort_values) != field_count for hit in hits):
        return False, 0, []
    rows = [hit.sort_values for hit in hits]
    type_rows = [sorted({_json_type(row[index]) for row in rows}) for index in range(field_count)]
    for row in rows:
        if not isinstance(row[0], (int, float)) or isinstance(row[0], bool):
            return False, 0, type_rows
        if field_count == 2 and not isinstance(row[1], str):
            return False, 0, type_rows
    ties = _primary_tie_count(hits)
    for previous, current in zip(rows, rows[1:], strict=False):
        comparison = _compare_sort_tuples(previous, current)
        if comparison is None or comparison > 0:
            return False, ties, type_rows
    return True, ties, type_rows


def _evaluate_pagination(
    first: SourcePage,
    second: SourcePage,
    sort_count: int,
) -> tuple[CapabilityState, dict[str, object]]:
    evidence: dict[str, object] = {
        "first_page_hits": len(first.hits),
        "second_page_hits": len(second.hits),
        "two_non_empty_pages": bool(first.hits and second.hits),
        "cursor_received": first.cursor_final is not None,
        "cursor_advanced": bool(
            first.cursor_final is not None
            and second.cursor_final is not None
            and first.cursor_final != second.cursor_final
        ),
    }
    if (
        not first.hits
        or not second.hits
        or first.cursor_final is None
        or second.cursor_final is None
    ):
        return CapabilityState.INCONCLUSIVE, evidence
    first_ids = _document_ids(first.hits)
    second_ids = _document_ids(second.hits)
    overlap = len(first_ids.intersection(second_ids))
    evidence["overlap_by_source_document_id"] = overlap
    valid_first, _, _ = _sort_observation(first.hits, sort_count)
    valid_second, _, _ = _sort_observation(second.hits, sort_count)
    boundary_order = _compare_sort_tuples(first.hits[-1].sort_values, second.hits[0].sort_values)
    evidence["ordered_within_pages"] = valid_first and valid_second
    evidence["ordered_across_boundary"] = boundary_order is not None and boundary_order < 0
    if not evidence["cursor_advanced"] or overlap or not valid_first or not valid_second:
        return CapabilityState.INCOMPATIBLE, evidence
    if not evidence["ordered_across_boundary"]:
        return CapabilityState.INCOMPATIBLE, evidence
    return CapabilityState.VALIDATED, evidence


def _document_ids(hits: tuple[SourceHit, ...]) -> set[str]:
    return {hit.source_id for hit in hits if isinstance(hit.source_id, str) and hit.source_id}


def _primary_tie_count(hits: tuple[SourceHit, ...]) -> int:
    values = [hit.sort_values[0] for hit in hits if hit.sort_values]
    return len(values) - len(set(values))


def _compare_sort_tuples(left: Cursor, right: Cursor) -> int | None:
    if len(left) != len(right):
        return None
    for left_value, right_value in zip(left, right, strict=True):
        if (
            isinstance(left_value, (int, float))
            and not isinstance(left_value, bool)
            and isinstance(right_value, (int, float))
            and not isinstance(right_value, bool)
        ):
            if left_value < right_value:
                return -1
            if left_value > right_value:
                return 1
            continue
        if isinstance(left_value, str) and isinstance(right_value, str):
            if left_value < right_value:
                return -1
            if left_value > right_value:
                return 1
            continue
        return None
    return 0


def _has_essential_identity_fields(hit: SourceHit) -> bool:
    source = _source_mapping(hit)
    if source is None:
        return False
    source_id = source.get("id")
    process_number = source.get("numeroProcesso")
    return (
        isinstance(source_id, str)
        and bool(source_id.strip())
        and isinstance(process_number, str)
        and _CNJ.fullmatch(process_number) is not None
        and source.get("tribunal") == "TJGO"
    )


def _first_cnj(hits: tuple[SourceHit, ...]) -> str | None:
    for hit in hits:
        source = _source_mapping(hit)
        number = source.get("numeroProcesso") if source is not None else None
        if isinstance(number, str) and _CNJ.fullmatch(number):
            return number
    return None


def _source_mapping(hit: SourceHit) -> Mapping[str, object] | None:
    if isinstance(hit.source, Mapping):
        return cast(Mapping[str, object], hit.source)
    return None


_MISSING = object()


def _get_source_path(source: Mapping[str, object] | None, path: str) -> object:
    if source is None:
        return _MISSING
    current: object = source
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _parse_source_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return date.fromisoformat(candidate[:10])
    except ValueError:
        return None


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "other"


def _sanitize_payload(value: object, *, parent_key: str | None = None) -> object:
    if parent_key == "search_after" and isinstance(value, list):
        return ["<cursor-redigido>" for _ in value]
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_payload(nested, parent_key=str(key))
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_payload(nested, parent_key=parent_key) for nested in value]
    if isinstance(value, str) and _CNJ.fullmatch(value):
        return "<numeroProcesso-observado-redigido>"
    return value


def _initial_dates() -> tuple[date, date]:
    to_date = datetime.now(UTC).date() + timedelta(days=1)
    return to_date - timedelta(days=365), to_date


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Informe a data no formato AAAA-MM-DD.") from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa até seis consultas sanitizadas de diagnóstico ao DataJud/TJGO."
    )
    parser.add_argument("--from-date", type=_parse_iso_date, help="Início inclusivo AAAA-MM-DD.")
    parser.add_argument("--to-date", type=_parse_iso_date, help="Fim exclusivo AAAA-MM-DD.")
    parser.add_argument("--output", type=Path, required=True, help="Caminho do relatório JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; absence of credentials produces a local report and nonzero status."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    default_from, default_to = _initial_dates()
    filed_from = args.from_date or default_from
    filed_to = args.to_date or default_to
    if filed_from >= filed_to:
        parser.error("--from-date precisa ser anterior a --to-date.")

    started_at = datetime.now(UTC)
    api_key = os.environ.get("DATAJUD_API_KEY")
    if api_key is None or not api_key.strip():
        report = missing_key_report(
            filed_from=filed_from,
            filed_to=filed_to,
            started_at=started_at,
        )
        write_report(report, args.output)
        print(f"DataJud não consultado: DATAJUD_API_KEY ausente. Relatório: {args.output}")
        return 2

    try:
        settings: Settings = get_settings()
    except ValidationError:
        print(
            "Configuração local inválida; nenhuma consulta foi enviada.",
            file=sys.stderr,
        )
        return 2
    if settings.environment != "real":
        report = missing_key_report(
            filed_from=filed_from,
            filed_to=filed_to,
            started_at=started_at,
            reason="AGROJUD_ENV deve ser real; nenhuma solicitação HTTP foi enviada.",
        )
        write_report(report, args.output)
        print(f"Probe exige AGROJUD_ENV=real. Relatório: {args.output}", file=sys.stderr)
        return 2

    try:
        adapter = DataJudSourceAdapter(settings)
    except SourceError as error:
        print(f"Probe não iniciado: {error.message}", file=sys.stderr)
        return 2
    with adapter:
        report = TJGOValidationProbe(
            adapter,
            filed_from=filed_from,
            filed_to=filed_to,
        ).run(started_at=started_at)
    write_report(report, args.output)
    conclusion = cast(str, report["conclusion"])
    request_error = any(
        request.get("outcome") == "ERROR"
        for request in cast(list[dict[str, object]], report["requests"])
    )
    print(
        f"Probe {conclusion}: {report['requests_made']} requisição(ões). Relatório: {args.output}"
    )
    return 1 if request_error or conclusion == "INCOMPATIBLE" else 0


def _report_conclusion(capabilities: Mapping[str, Mapping[str, object]]) -> str:
    states = {str(result.get("state")) for result in capabilities.values()}
    if CapabilityState.INCONCLUSIVE.value in states:
        return CapabilityState.INCONCLUSIVE.value
    if CapabilityState.INCOMPATIBLE.value in states:
        return CapabilityState.INCOMPATIBLE.value
    return CapabilityState.VALIDATED.value


if __name__ == "__main__":
    raise SystemExit(main())
