"""Bounded, read-only DataJud probe for the 10,000-hit page contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import create_engine, text

from agrojud.config import Settings, get_settings
from agrojud.sources.catalog import compile_preset
from agrojud.sources.contracts import (
    MAX_PAGE_SIZE,
    SOURCE_ID_TIEBREAKER_SORT,
    Cursor,
    SourceError,
    SourceQuery,
    build_datajud_payload,
)
from agrojud.sources.datajud import (
    DATAJUD_TJGO_ENDPOINT,
    DataJudProbeResponse,
    DataJudSourceAdapter,
)
from agrojud.sources.datajud_validation import write_report

PAGE_SIZE = MAX_PAGE_SIZE
MAX_REQUESTS = 12
MIN_REQUEST_INTERVAL_SECONDS = 0.5
MAX_WINDOW_DAYS = 31
PRESET_ID = "execucoes.amplas"
READY_WINDOW_MINUTES = 15

type ReadinessCheck = Callable[[], str | None]
type MonotonicClock = Callable[[], float]
type Sleep = Callable[[float], None]


def runtime_readiness_reason(settings: Settings) -> str | None:
    """Read-only guard: require an idle real collection queue before a remote probe."""

    engine = create_engine(
        settings.effective_database_url,
        hide_parameters=True,
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            active_or_ready = connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM jobs
                        WHERE source = 'datajud'
                          AND (
                              status = 'running'
                              OR (
                                  status IN ('queued', 'retry_wait')
                                  AND next_attempt_at <= clock_timestamp()
                              )
                          )
                    )
                    """
                )
            )
            pending_dispatch = connection.scalar(
                text("SELECT EXISTS (SELECT 1 FROM schedule_dispatches WHERE status = 'pending')")
            )
            saved_search_due = connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM saved_searches
                        WHERE enabled
                          AND next_run_at <= clock_timestamp() + make_interval(mins => :minutes)
                    )
                    """
                ),
                {"minutes": READY_WINDOW_MINUTES},
            )
            source_limited = connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM source_rate_limits
                        WHERE source = 'datajud'
                          AND (
                              next_request_at > clock_timestamp()
                              OR cooldown_until > clock_timestamp()
                          )
                    )
                    """
                )
            )
    except Exception:
        return "RUNTIME_READINESS_UNAVAILABLE"
    finally:
        engine.dispose()

    if active_or_ready:
        return "DATAJUD_JOB_ACTIVE_OR_READY"
    if pending_dispatch:
        return "SCHEDULE_DISPATCH_PENDING"
    if saved_search_due:
        return "SAVED_SEARCH_DUE_WITHIN_15_MINUTES"
    if source_limited:
        return "DATAJUD_RATE_LIMIT_OR_COOLDOWN_ACTIVE"
    return None


class DataJudPageSizeProbe:
    """Make at most two sequential requests and retain only sanitized measurements."""

    def __init__(
        self,
        adapter: DataJudSourceAdapter,
        query: SourceQuery,
        *,
        readiness_check: ReadinessCheck,
        sleep: Sleep = time.sleep,
        monotonic: MonotonicClock = time.monotonic,
    ) -> None:
        self._adapter = adapter
        self._query = query
        self._readiness_check = readiness_check
        self._sleep = sleep
        self._monotonic = monotonic
        self._request_started_at: float | None = None
        self._request_records: list[dict[str, object]] = []

    def run(self, *, started_at: datetime | None = None) -> dict[str, object]:
        started = started_at or datetime.now(UTC)
        run_started = perf_counter()
        state = "INCONCLUSIVE"
        reason: str | None = None
        first_page: DataJudProbeResponse | None = None

        try:
            reason = self._readiness_check()
            if reason is None:
                first_page, reason = self._request_page(None, page_number=1)
            if reason is None and first_page is not None:
                if len(first_page.page.hits) != PAGE_SIZE:
                    reason = "FIRST_PAGE_DID_NOT_RETURN_10000_HITS"
                elif first_page.page.cursor_final is None:
                    reason = "FIRST_PAGE_CURSOR_MISSING"

            if reason is None and first_page is not None:
                reason = self._readiness_check()
                if reason is None:
                    second_page, reason = self._request_page(
                        first_page.page.cursor_final,
                        page_number=2,
                    )
                    if reason is None and second_page is not None:
                        if not second_page.page.hits:
                            reason = "CONTINUATION_PAGE_EMPTY"
                        elif second_page.page.cursor_final is None:
                            reason = "CONTINUATION_CURSOR_MISSING"
                        elif second_page.page.cursor_final == first_page.page.cursor_final:
                            reason = "CURSOR_DID_NOT_ADVANCE"
                        else:
                            state = "VALIDATED"
        finally:
            self._wait_for_shared_interval()

        return {
            "report_version": 1,
            "kind": "datajud_page_size_probe",
            "started_at": started.astimezone(UTC).isoformat(),
            "endpoint": DATAJUD_TJGO_ENDPOINT,
            "tribunal": self._query.tribunal,
            "preset_id": self._query.preset_id,
            "preset_version": self._query.preset_version,
            "filed_from": self._query.filed_from.isoformat()
            if self._query.filed_from is not None
            else None,
            "filed_to_exclusive": self._query.filed_to.isoformat()
            if self._query.filed_to is not None
            else None,
            "sort_fields": [term.field_name for term in self._query.sort],
            "request_budget": {
                "page_size": PAGE_SIZE,
                "max_requests": MAX_REQUESTS,
                "requests_made": len(self._request_records),
                "minimum_interval_seconds": MIN_REQUEST_INTERVAL_SECONDS,
                "automatic_retries": False,
                "writes_to_product_database": False,
            },
            "requests": self._request_records,
            "duration_ms": max(0, round((perf_counter() - run_started) * 1000)),
            "state": state,
            "reason_code": reason,
        }

    def _request_page(
        self,
        cursor: Cursor | None,
        *,
        page_number: int,
    ) -> tuple[DataJudProbeResponse | None, str | None]:
        if len(self._request_records) >= MAX_REQUESTS:
            return None, "REQUEST_BUDGET_EXHAUSTED"
        if page_number > 1:
            readiness_reason = self._readiness_check()
            if readiness_reason is not None:
                return None, readiness_reason
        self._wait_for_shared_interval()
        request_started = self._monotonic()
        self._request_started_at = request_started
        try:
            response = self._adapter.fetch_probe_page(self._query, cursor, PAGE_SIZE)
        except SourceError as error:
            self._request_records.append(
                {
                    "page": page_number,
                    "page_size": PAGE_SIZE,
                    "outcome": "ERROR",
                    "http_status": error.status_code,
                    "error_code": error.code.value,
                    "response_bytes": 0,
                    "duration_ms": max(0, round((self._monotonic() - request_started) * 1000)),
                    "cursor_sha256": _cursor_digest(cursor),
                }
            )
            return None, f"UPSTREAM_{error.code.value}"

        self._request_records.append(
            {
                "page": page_number,
                "page_size": PAGE_SIZE,
                "outcome": "SUCCESS",
                "http_status": response.status_code,
                "hit_count": len(response.page.hits),
                "total_value": response.page.total_value,
                "total_relation": response.page.total_relation,
                "response_bytes": response.response_bytes,
                "duration_ms": max(0, round((self._monotonic() - request_started) * 1000)),
                "cursor_sha256": _cursor_digest(response.page.cursor_final),
            }
        )
        if len(response.page.hits) > PAGE_SIZE:
            return None, "RESPONSE_EXCEEDED_REQUESTED_PAGE_SIZE"
        return response, None

    def _wait_for_shared_interval(self) -> None:
        if self._request_started_at is None:
            return
        elapsed = self._monotonic() - self._request_started_at
        remaining = MIN_REQUEST_INTERVAL_SECONDS - elapsed
        if remaining > 0:
            self._sleep(remaining)


def _cursor_digest(cursor: Cursor | None) -> str | None:
    if cursor is None:
        return None
    serialized = json.dumps(cursor, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Informe a data no formato AAAA-MM-DD.") from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida page_size 10.000 e search_after em uma probe TJGO limitada."
    )
    parser.add_argument("--from-date", type=_parse_iso_date, required=True)
    parser.add_argument("--through-date", type=_parse_iso_date, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--confirm-terms-reviewed",
        action="store_true",
        help="Confirma revisão dos termos/finalidade vigentes antes da probe real.",
    )
    return parser


def _inconclusive_report(
    *,
    started_at: datetime,
    filed_from: date,
    filed_to_exclusive: date,
    reason: str,
    terms_reviewed: bool,
) -> dict[str, object]:
    return {
        "report_version": 1,
        "kind": "datajud_page_size_probe",
        "started_at": started_at.astimezone(UTC).isoformat(),
        "endpoint": DATAJUD_TJGO_ENDPOINT,
        "tribunal": "TJGO",
        "preset_id": PRESET_ID,
        "filed_from": filed_from.isoformat(),
        "filed_to_exclusive": filed_to_exclusive.isoformat(),
        "request_budget": {
            "page_size": PAGE_SIZE,
            "max_requests": MAX_REQUESTS,
            "requests_made": 0,
            "minimum_interval_seconds": MIN_REQUEST_INTERVAL_SECONDS,
            "automatic_retries": False,
            "writes_to_product_database": False,
        },
        "terms_review": {"operator_confirmed_before_probe": terms_reviewed},
        "requests": [],
        "duration_ms": 0,
        "state": "INCONCLUSIVE",
        "reason_code": reason,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run only after explicit terms confirmation and an idle-queue preflight."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    filed_to_exclusive = args.through_date + timedelta(days=1)
    if args.from_date >= filed_to_exclusive:
        parser.error("--from-date precisa ser anterior a --through-date.")
    if (filed_to_exclusive - args.from_date).days > MAX_WINDOW_DAYS:
        parser.error(f"A janela da probe é limitada a {MAX_WINDOW_DAYS} dias.")

    started_at = datetime.now(UTC)
    if not args.confirm_terms_reviewed:
        report = _inconclusive_report(
            started_at=started_at,
            filed_from=args.from_date,
            filed_to_exclusive=filed_to_exclusive,
            reason="TERMS_REVIEW_REQUIRED",
            terms_reviewed=False,
        )
        write_report(report, args.output)
        print("Probe não enviada: falta confirmar a revisão dos termos vigentes.", file=sys.stderr)
        return 2

    api_key = os.environ.get("DATAJUD_API_KEY")
    if api_key is None or not api_key.strip():
        report = _inconclusive_report(
            started_at=started_at,
            filed_from=args.from_date,
            filed_to_exclusive=filed_to_exclusive,
            reason="DATAJUD_API_KEY_MISSING",
            terms_reviewed=True,
        )
        write_report(report, args.output)
        print("Probe não enviada: DATAJUD_API_KEY ausente.", file=sys.stderr)
        return 2

    try:
        settings = get_settings()
    except ValidationError:
        report = _inconclusive_report(
            started_at=started_at,
            filed_from=args.from_date,
            filed_to_exclusive=filed_to_exclusive,
            reason="REAL_CONFIGURATION_INVALID",
            terms_reviewed=True,
        )
        write_report(report, args.output)
        print("Probe não enviada: configuração real inválida.", file=sys.stderr)
        return 2
    if settings.environment != "real":
        reason: str | None = "AGROJUD_ENV_MUST_BE_REAL"
    else:
        reason = runtime_readiness_reason(settings)
    if reason is not None:
        report = _inconclusive_report(
            started_at=started_at,
            filed_from=args.from_date,
            filed_to_exclusive=filed_to_exclusive,
            reason=reason,
            terms_reviewed=True,
        )
        write_report(report, args.output)
        print(f"Probe não enviada: {reason}.", file=sys.stderr)
        return 2

    try:
        compiled = compile_preset(
            PRESET_ID,
            environment="real",
            tribunal="TJGO",
            filed_from=args.from_date,
            filed_through_inclusive=args.through_date,
        )
        query = SourceQuery(
            tribunal=compiled.query.tribunal,
            filed_from=compiled.query.filed_from,
            filed_to=compiled.query.filed_to,
            class_codes=compiled.query.class_codes,
            subject_codes=compiled.query.subject_codes,
            movement_codes=compiled.query.movement_codes,
            preset_id=compiled.query.preset_id,
            preset_version=compiled.query.preset_version,
            sort=SOURCE_ID_TIEBREAKER_SORT,
        )
        adapter = DataJudSourceAdapter(settings)
    except SourceError as error:
        report = _inconclusive_report(
            started_at=started_at,
            filed_from=args.from_date,
            filed_to_exclusive=filed_to_exclusive,
            reason=f"PRESET_OR_ADAPTER_{error.code.value}",
            terms_reviewed=True,
        )
        write_report(report, args.output)
        print("Probe não enviada: preset ou adaptador indisponível.", file=sys.stderr)
        return 2

    probe = DataJudPageSizeProbe(
        adapter,
        query,
        readiness_check=lambda: runtime_readiness_reason(settings),
    )
    try:
        report = probe.run(started_at=started_at)
    finally:
        adapter.close()
    report["terms_review"] = {"operator_confirmed_before_probe": True}
    report["criteria"] = {
        "preset_justification": compiled.item.justification,
        "page_size_requirement": PAGE_SIZE,
        "continuation_requirement": "segunda página não vazia sob query e sort congelados",
    }
    report["planned_query"] = _sanitized_planned_query(query)
    write_report(report, args.output)
    request_budget = cast(dict[str, object], report["request_budget"])
    print(
        f"Probe DataJud: {report['state']}; "
        f"{request_budget['requests_made']} requisição(ões). Relatório: {args.output}"
    )
    return 0 if report["state"] == "VALIDATED" else 1


def _sanitized_planned_query(query: SourceQuery) -> Mapping[str, Any]:
    from agrojud.sources.datajud_validation import sanitize_payload

    return cast(
        Mapping[str, Any],
        sanitize_payload(build_datajud_payload(query, None, PAGE_SIZE)),
    )


if __name__ == "__main__":
    raise SystemExit(main())
