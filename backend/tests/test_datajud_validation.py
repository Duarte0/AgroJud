"""Tests for the bounded, sanitized TJGO validation probe."""

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agrojud.config import Settings
from agrojud.sources import DataJudSourceAdapter, SourceErrorCode
from agrojud.sources.datajud_validation import (
    PAGE_SIZE,
    TJGOValidationProbe,
    main,
    missing_key_report,
    write_report,
)

API_KEY = "do-not-leak-probe-key"
DATABASE_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_demo"
TEST_DATABASE_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_probe_test"
FILED_FROM = date(2025, 10, 8)
FILED_TO = date(2026, 10, 9)
CASE_A = "10000000000000000001"
CASE_B = "10000000000000000002"
CASE_C = "10000000000000000003"
SOURCE_A = "TJGO_100_G1_1_10000000000000000001"
SOURCE_B = "TJGO_100_G1_1_10000000000000000002"
SOURCE_C = "TJGO_100_G1_1_10000000000000000003"


def make_settings() -> Settings:
    return Settings(
        environment="test",
        database_url=DATABASE_URL,
        test_database_url=TEST_DATABASE_URL,
        datajud_api_key=SecretStr(API_KEY),
    )


def hit(case_number: str, source_id: str, sort_values: list[object]) -> dict[str, object]:
    return {
        "_id": source_id,
        "_source": {
            "id": source_id,
            "numeroProcesso": case_number,
            "tribunal": "TJGO",
            "dataAjuizamento": "2025-12-01T00:00:00",
            "grau": "G1",
            "classe": {"codigo": 100},
            "assuntos": [{"codigo": 200}],
            "orgaoJulgador": {"codigo": 1},
            "movimentos": [],
            "@timestamp": "2025-12-02T00:00:00",
            "campoPrivado": "nao-deve-aparecer-no-relatorio",
        },
        "sort": sort_values,
    }


def response_body(hits: list[dict[str, object]]) -> dict[str, object]:
    return {"hits": {"total": {"value": len(hits), "relation": "eq"}, "hits": hits}}


def ordered_transport(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body: dict[str, Any] = json.loads(request.content)
        sort_fields = [next(iter(term)) for term in body["sort"]]
        cursor = body.get("search_after")
        filters = body["query"]["bool"]["filter"]
        observed_number = next(
            (
                clause["match"]["numeroProcesso"]
                for clause in filters
                if "match" in clause and "numeroProcesso" in clause["match"]
            ),
            None,
        )

        if observed_number is not None:
            sort_values = [100] if len(sort_fields) == 1 else [100, SOURCE_A]
            return httpx.Response(200, json=response_body([hit(CASE_A, SOURCE_A, sort_values)]))
        if len(sort_fields) == 2:
            if cursor is not None:
                return httpx.Response(
                    200,
                    json=response_body([hit(CASE_C, SOURCE_C, [200, SOURCE_C])]),
                )
            return httpx.Response(
                200,
                json=response_body(
                    [
                        hit(CASE_A, SOURCE_A, [100, SOURCE_A]),
                        hit(CASE_B, SOURCE_B, [100, SOURCE_B]),
                    ]
                ),
            )
        if cursor is not None:
            return httpx.Response(200, json=response_body([hit(CASE_C, SOURCE_C, [200])]))
        return httpx.Response(
            200,
            json=response_body([hit(CASE_A, SOURCE_A, [100]), hit(CASE_B, SOURCE_B, [100])]),
        )

    return httpx.MockTransport(handler)


def test_probe_stays_within_six_calls_and_sanitizes_every_report_field(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []
    adapter = DataJudSourceAdapter(make_settings(), transport=ordered_transport(requests))
    try:
        report = TJGOValidationProbe(
            adapter,
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
        ).run(started_at=datetime(2026, 10, 8, tzinfo=UTC))
    finally:
        adapter.close()

    serialized = json.dumps(report, ensure_ascii=False)
    output = tmp_path / "evidence" / "probe.json"
    write_report(report, output)
    saved = output.read_text(encoding="utf-8")

    assert len(requests) == report["requests_made"] == 6
    assert report["request_budget"] == {
        "max_requests": 6,
        "page_size": 100,
        "automatic_retries": False,
        "writes_to_product_database": False,
    }
    assert all(json.loads(request.content)["size"] == PAGE_SIZE for request in requests)
    assert all(
        str(request.url) == "https://api-publica.datajud.cnj.jus.br/api_publica_tjgo/_search"
        for request in requests
    )
    assert API_KEY not in serialized
    assert API_KEY not in saved
    assert CASE_A not in serialized and CASE_A not in saved
    assert SOURCE_A not in serialized and SOURCE_A not in saved
    assert "nao-deve-aparecer-no-relatorio" not in serialized
    assert "campoPrivado" not in serialized
    assert "<numeroProcesso-observado-redigido>" in saved
    assert "<cursor-redigido>" in saved

    capabilities = report["capabilities"]
    assert isinstance(capabilities, dict)
    assert capabilities["envelope"]["state"] == "VALIDATED"
    assert capabilities["essential_fields"]["state"] == "VALIDATED"
    assert capabilities["filters"]["state"] == "VALIDATED"
    assert capabilities["query_by_case_number"]["state"] == "VALIDATED"
    assert capabilities["source_identity"]["state"] == "VALIDATED"
    assert capabilities["sort_timestamp"]["state"] == "VALIDATED"
    assert capabilities["compound_sort"]["state"] == "VALIDATED"
    assert capabilities["pagination"]["state"] == "VALIDATED"
    pagination_evidence = capabilities["pagination"]["evidence"]
    assert pagination_evidence["two_non_empty_pages"] is True
    assert pagination_evidence["cursor_advanced"] is True
    assert pagination_evidence["overlap_by_source_document_id"] == 0
    assert report["repeat_comparison"]["overlap_by_source_document_id"] == 2


def test_missing_key_report_has_no_requests_and_marks_external_capabilities_inconclusive() -> None:
    report = missing_key_report(filed_from=FILED_FROM, filed_to=FILED_TO)

    assert report["requests_made"] == 0
    assert report["requests"] == []
    assert report["conclusion"] == "INCONCLUSIVE"
    capabilities = report["capabilities"]
    assert isinstance(capabilities, dict)
    assert all(result["state"] == "INCONCLUSIVE" for result in capabilities.values())


def test_cli_without_key_fails_locally_and_writes_sanitized_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    output = tmp_path / "probe.json"

    exit_code = main(
        [
            "--from-date",
            FILED_FROM.isoformat(),
            "--to-date",
            FILED_TO.isoformat(),
            "--output",
            str(output),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert report["requests_made"] == 0
    assert report["conclusion"] == "INCONCLUSIVE"


@pytest.mark.parametrize(
    ("status", "expected_code", "expected_envelope"),
    [
        (401, SourceErrorCode.AUTHENTICATION, "INCONCLUSIVE"),
        (429, SourceErrorCode.RATE_LIMIT, "INCONCLUSIVE"),
        (200, SourceErrorCode.CONTRACT, "INCOMPATIBLE"),
    ],
)
def test_probe_reports_authentication_rate_limit_and_invalid_envelope(
    status: int,
    expected_code: SourceErrorCode,
    expected_envelope: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status == 200:
            return httpx.Response(200, json={"hits": {"hits": "invalid"}})
        headers = {"Retry-After": "19"} if status == 429 else {}
        return httpx.Response(status, headers=headers, content=b"private response body")

    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(handler))
    try:
        report = TJGOValidationProbe(
            adapter,
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
        ).run()
    finally:
        adapter.close()

    assert len(requests) == report["requests_made"] == 1
    request_summary = report["requests"][0]
    assert request_summary["error_code"] == expected_code.value
    assert request_summary["http_status"] == status
    assert "private response body" not in json.dumps(report)
    assert report["capabilities"]["envelope"]["state"] == expected_envelope


def test_probe_timeout_stops_without_retry_and_records_no_empty_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("timeout with private value")

    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(handler))
    caplog.set_level(logging.WARNING, logger="agrojud.sources.datajud")
    try:
        report = TJGOValidationProbe(
            adapter,
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
        ).run()
    finally:
        adapter.close()

    assert len(requests) == report["requests_made"] == 1
    assert report["requests"][0]["outcome"] == "ERROR"
    assert report["requests"][0]["error_code"] == SourceErrorCode.NETWORK.value
    assert "records=0" not in json.dumps(report)
    assert "timeout with private value" not in caplog.text


def test_rejected_compound_sort_is_incompatible_and_timestamp_ties_hold_pagination_open() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body: dict[str, Any] = json.loads(request.content)
        sort_fields = [next(iter(term)) for term in body["sort"]]
        cursor = body.get("search_after")
        if len(sort_fields) == 2:
            return httpx.Response(400, content=b"unsupported sort detail")
        if any(
            "numeroProcesso" in clause.get("match", {})
            for clause in body["query"]["bool"]["filter"]
        ):
            return httpx.Response(200, json=response_body([hit(CASE_A, SOURCE_A, [100])]))
        if cursor is not None:
            return httpx.Response(200, json=response_body([hit(CASE_C, SOURCE_C, [300])]))
        return httpx.Response(
            200,
            json=response_body([hit(CASE_A, SOURCE_A, [100]), hit(CASE_B, SOURCE_B, [100])]),
        )

    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(handler))
    try:
        report = TJGOValidationProbe(
            adapter,
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
        ).run()
    finally:
        adapter.close()

    assert len(requests) == 5
    assert report["capabilities"]["compound_sort"]["state"] == "INCOMPATIBLE"
    assert report["capabilities"]["pagination"]["state"] == "INCONCLUSIVE"
    assert report["capabilities"]["pagination"]["evidence"]["timestamp_ties_observed"] > 0


def test_repeated_cursor_is_reported_as_incompatible_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body: dict[str, Any] = json.loads(request.content)
        sort_fields = [next(iter(term)) for term in body["sort"]]
        cursor = body.get("search_after")
        if len(sort_fields) == 2:
            return httpx.Response(400, content=b"unsupported sort detail")
        if any(
            "numeroProcesso" in clause.get("match", {})
            for clause in body["query"]["bool"]["filter"]
        ):
            return httpx.Response(200, json=response_body([hit(CASE_A, SOURCE_A, [100])]))
        if cursor is not None:
            return httpx.Response(200, json=response_body([hit(CASE_C, SOURCE_C, [200])]))
        return httpx.Response(
            200,
            json=response_body([hit(CASE_A, SOURCE_A, [100]), hit(CASE_B, SOURCE_B, [200])]),
        )

    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(handler))
    try:
        report = TJGOValidationProbe(
            adapter,
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
        ).run()
    finally:
        adapter.close()

    assert report["capabilities"]["pagination"]["state"] == "INCOMPATIBLE"
    assert report["capabilities"]["pagination"]["evidence"]["cursor_advanced"] is False
