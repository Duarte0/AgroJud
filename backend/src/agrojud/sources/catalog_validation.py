"""Bounded, sanitized S5 probe for thematic catalog filters against DataJud/TJGO."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import cast

from pydantic import ValidationError

from agrojud.config import Settings, get_settings
from agrojud.sources.catalog import CatalogItem, FilterDimension, load_catalog
from agrojud.sources.contracts import (
    Cursor,
    SourceError,
    SourceErrorCode,
    SourceHit,
    SourcePage,
    SourceQuery,
    build_datajud_payload,
)
from agrojud.sources.datajud import DATAJUD_TJGO_ENDPOINT, DataJudSourceAdapter
from agrojud.sources.datajud_validation import (
    CapabilityState,
    sanitize_payload,
    write_report,
)

PAGE_SIZE = 100
MAX_REQUESTS_PER_ITEM = 2
MAX_WINDOW_DAYS = 31
MIN_DISTINCT_EXAMPLES = 3
REQUEST_SPACING_SECONDS = 1.0
_STOP_CODES = {
    SourceErrorCode.AUTHENTICATION,
    SourceErrorCode.AUTHORIZATION,
    SourceErrorCode.RATE_LIMIT,
}
_DIMENSION_FIELDS: Mapping[FilterDimension, str] = {
    "class": "classe",
    "subject": "assuntos",
    "movement": "movimentos",
}


class CatalogValidationProbe:
    """Query each catalog item once or twice and classify filter and sample evidence."""

    def __init__(
        self,
        adapter: DataJudSourceAdapter,
        *,
        filed_from: date,
        filed_to: date,
        items: Sequence[CatalogItem],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if filed_from >= filed_to:
            raise ValueError("A data inicial precisa ser anterior à data final exclusiva.")
        if (filed_to - filed_from).days > MAX_WINDOW_DAYS:
            raise ValueError(f"O recorte da probe S5 é limitado a {MAX_WINDOW_DAYS} dias.")
        self._adapter = adapter
        self._filed_from = filed_from
        self._filed_to = filed_to
        self._items = tuple(items)
        self._sleep = sleep
        self._requests_made = 0
        self._stopped_reason: str | None = None

    def run(self, *, started_at: datetime | None = None) -> dict[str, object]:
        started = started_at or datetime.now(UTC)
        run_started = perf_counter()
        results = [self._assess_item(item) for item in self._items]
        return {
            "report_version": 1,
            "kind": "s5_catalog_probe",
            "started_at": started.astimezone(UTC).isoformat(),
            "endpoint": DATAJUD_TJGO_ENDPOINT,
            "catalog_version": load_catalog().catalog_version,
            "filed_from": self._filed_from.isoformat(),
            "filed_to_exclusive": self._filed_to.isoformat(),
            "criteria": {
                "query_status": (
                    "VALIDATED quando total > 0 e todos os hits observados contêm algum código "
                    "pedido em cada dimensão filtrada; INCOMPATIBLE se algum hit não contém; "
                    "INCONCLUSIVE sem hits ou sem resposta."
                ),
                "sample_status": (
                    f"VALIDATED com ao menos {MIN_DISTINCT_EXAMPLES} documentos distintos "
                    "contendo o código estruturado; INCONCLUSIVE abaixo disso."
                ),
            },
            "request_budget": {
                "max_requests_per_item": MAX_REQUESTS_PER_ITEM,
                "page_size": PAGE_SIZE,
                "spacing_seconds": REQUEST_SPACING_SECONDS,
                "automatic_retries": False,
                "writes_to_product_database": False,
            },
            "requests_made": self._requests_made,
            "duration_ms": _elapsed_ms(run_started),
            "stopped_reason": self._stopped_reason,
            "items": results,
        }

    def _assess_item(self, item: CatalogItem) -> dict[str, object]:
        query = SourceQuery(
            filed_from=self._filed_from,
            filed_to=self._filed_to,
            class_codes=item.filters["class"],
            subject_codes=item.filters["subject"],
            movement_codes=item.filters["movement"],
        )
        requests: list[dict[str, object]] = []
        hits: list[SourceHit] = []
        total: dict[str, object] | None = None
        failure: SourceError | None = None
        cursor: Cursor | None = None
        for page_number in range(1, MAX_REQUESTS_PER_ITEM + 1):
            if self._stopped_reason is not None:
                break
            page, error, record = self._request(query, cursor, page_number)
            requests.append(record)
            if error is not None:
                failure = error
                break
            assert page is not None
            if page_number == 1:
                total = {"value": page.total_value, "relation": page.total_relation}
            hits.extend(page.hits)
            if (
                _distinct_matching(item, hits) >= MIN_DISTINCT_EXAMPLES
                or len(page.hits) < PAGE_SIZE
                or page.cursor_final is None
            ):
                break
            cursor = page.cursor_final

        query_status, sample_status = _classify(item, hits, failure, self._stopped_reason)
        return {
            "id": item.id,
            "kind": item.kind,
            "version": item.version,
            "filters": {dimension: list(codes) for dimension, codes in item.filters.items()},
            "total": total,
            "requests": requests,
            "query_status": query_status,
            "sample_status": sample_status,
        }

    def _request(
        self, query: SourceQuery, cursor: Cursor | None, page_number: int
    ) -> tuple[SourcePage | None, SourceError | None, dict[str, object]]:
        if self._requests_made:
            self._sleep(REQUEST_SPACING_SECONDS)
        payload = sanitize_payload(build_datajud_payload(query, cursor, PAGE_SIZE))
        self._requests_made += 1
        started = perf_counter()
        try:
            response = self._adapter.fetch_probe_page(query, cursor, PAGE_SIZE)
        except SourceError as error:
            if error.code in _STOP_CODES:
                self._stopped_reason = f"{error.code.value}: probe interrompida sem retry."
            return (
                None,
                error,
                {
                    "page": page_number,
                    "query": payload,
                    "outcome": "ERROR",
                    "error_code": error.code.value,
                    "http_status": error.status_code,
                    "duration_ms": _elapsed_ms(started),
                },
            )
        return (
            response.page,
            None,
            {
                "page": page_number,
                "query": payload,
                "outcome": "SUCCESS",
                "http_status": response.status_code,
                "duration_ms": _elapsed_ms(started),
                "hit_count": len(response.page.hits),
            },
        )


def _classify(
    item: CatalogItem,
    hits: Sequence[SourceHit],
    failure: SourceError | None,
    stopped_reason: str | None,
) -> tuple[dict[str, object], dict[str, object]]:
    if not hits:
        if failure is not None:
            reason = f"Consulta sem resposta utilizável ({failure.code.value}); sem inferência."
        elif stopped_reason is not None:
            reason = f"Item não consultado: {stopped_reason}"
        else:
            reason = (
                "A consulta não retornou hits no recorte; ausência não prova incompatibilidade."
            )
        inconclusive: dict[str, object] = {
            "state": CapabilityState.INCONCLUSIVE.value,
            "evidence": {"reason": reason},
        }
        return inconclusive, dict(inconclusive)

    matched_codes: dict[str, Counter[int]] = {
        dimension: Counter() for dimension, codes in item.filters.items() if codes
    }
    matching_ids: set[str] = set()
    mismatched = 0
    for hit in hits:
        hit_matches = True
        for dimension, codes in item.filters.items():
            if not codes:
                continue
            found = set(_codes(hit, dimension)).intersection(codes)
            if not found:
                hit_matches = False
                continue
            matched_codes[dimension].update(found)
        if hit_matches:
            matching_ids.add(_document_key(hit))
        else:
            mismatched += 1

    code_counts = {
        dimension: {str(code): count for code, count in sorted(counter.items())}
        for dimension, counter in matched_codes.items()
    }
    query_evidence: dict[str, object] = {
        "sample_count": len(hits),
        "matching_hits": len(hits) - mismatched,
        "mismatched_hits": mismatched,
        "matched_codes": code_counts,
    }
    if mismatched:
        query_evidence["reason"] = "Há hits sem nenhum código pedido em uma dimensão filtrada."
        query_state = CapabilityState.INCOMPATIBLE
    else:
        query_evidence["reason"] = "Todos os hits observados contêm um código pedido por dimensão."
        query_state = CapabilityState.VALIDATED

    distinct = len(matching_ids)
    sample_evidence: dict[str, object] = {
        "distinct_matching_documents": distinct,
        "minimum_required": MIN_DISTINCT_EXAMPLES,
        "matched_codes": code_counts,
    }
    if distinct >= MIN_DISTINCT_EXAMPLES:
        sample_state = CapabilityState.VALIDATED
        sample_evidence["reason"] = "Exemplos estruturados reais do TJGO observados no recorte."
    else:
        sample_state = CapabilityState.INCONCLUSIVE
        sample_evidence["reason"] = "Exemplos estruturados insuficientes no recorte observado."
    return (
        {"state": query_state.value, "evidence": query_evidence},
        {"state": sample_state.value, "evidence": sample_evidence},
    )


def _distinct_matching(item: CatalogItem, hits: Sequence[SourceHit]) -> int:
    return len(
        {
            _document_key(hit)
            for hit in hits
            if all(
                set(_codes(hit, dimension)).intersection(codes)
                for dimension, codes in item.filters.items()
                if codes
            )
        }
    )


def _document_key(hit: SourceHit) -> str:
    raw_id = hit.raw.get("_id")
    return raw_id if isinstance(raw_id, str) else repr(hit.source_id)


def _codes(hit: SourceHit, dimension: FilterDimension) -> Iterator[int]:
    source = hit.source
    if not isinstance(source, Mapping):
        return
    yield from _nested_codes(source.get(_DIMENSION_FIELDS[dimension]))


def _nested_codes(value: object) -> Iterator[int]:
    # DataJud returns classe as an object and assuntos/movimentos as lists, sometimes nested.
    if isinstance(value, Mapping):
        code = value.get("codigo")
        if isinstance(code, int) and not isinstance(code, bool):
            yield code
        elif isinstance(code, str) and code.isdigit():
            yield int(code)
    elif isinstance(value, list):
        for nested in value:
            yield from _nested_codes(nested)


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Informe a data no formato AAAA-MM-DD.") from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida filtros e exemplos do catálogo temático no DataJud/TJGO (S5)."
    )
    parser.add_argument("--from-date", type=_parse_iso_date, help="Início inclusivo AAAA-MM-DD.")
    parser.add_argument("--to-date", type=_parse_iso_date, help="Fim exclusivo AAAA-MM-DD.")
    parser.add_argument(
        "--item",
        action="append",
        dest="items",
        help="Restringe a probe a um item do catálogo; pode ser repetido.",
    )
    parser.add_argument("--output", type=Path, required=True, help="Caminho do relatório JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; refuses to run without real configuration and a DataJud key."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    filed_to = args.to_date or datetime.now(UTC).date()
    filed_from = args.from_date or filed_to - timedelta(days=MAX_WINDOW_DAYS)
    if filed_from >= filed_to:
        parser.error("--from-date precisa ser anterior a --to-date.")
    if (filed_to - filed_from).days > MAX_WINDOW_DAYS:
        parser.error(f"O recorte é limitado a {MAX_WINDOW_DAYS} dias.")

    catalog_items = load_catalog().items
    known = {item.id for item in catalog_items}
    selected = set(args.items or known)
    unknown = sorted(selected - known)
    if unknown:
        parser.error(f"Itens desconhecidos no catálogo: {', '.join(unknown)}")
    items = tuple(item for item in catalog_items if item.id in selected)

    api_key = os.environ.get("DATAJUD_API_KEY")
    if api_key is None or not api_key.strip():
        print("DataJud não consultado: DATAJUD_API_KEY ausente.", file=sys.stderr)
        return 2
    try:
        settings: Settings = get_settings()
    except ValidationError:
        print("Configuração local inválida; nenhuma consulta foi enviada.", file=sys.stderr)
        return 2
    if settings.environment != "real":
        print("Probe exige AGROJUD_ENV=real; nenhuma consulta foi enviada.", file=sys.stderr)
        return 2

    try:
        adapter = DataJudSourceAdapter(settings)
    except SourceError as error:
        print(f"Probe não iniciada: {error.message}", file=sys.stderr)
        return 2
    with adapter:
        report = CatalogValidationProbe(
            adapter, filed_from=filed_from, filed_to=filed_to, items=items
        ).run()
    write_report(report, args.output)
    summary = ", ".join(
        f"{result['id']}={_state(result, 'query_status')}/{_state(result, 'sample_status')}"
        for result in cast(list[dict[str, object]], report["items"])
    )
    print(f"Probe S5: {report['requests_made']} requisição(ões). {summary}.")
    print(f"Relatório: {args.output}")
    return 1 if report["stopped_reason"] is not None else 0


def _state(result: Mapping[str, object], name: str) -> str:
    return str(cast(Mapping[str, object], result[name])["state"])


if __name__ == "__main__":
    raise SystemExit(main())
