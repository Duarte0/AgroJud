"""HTTPX transport tests for the single-attempt DataJud adapter."""

import json
import logging
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from agrojud.config import Settings
from agrojud.sources import (
    DataJudSourceAdapter,
    SourceError,
    SourceErrorCode,
    SourceQuery,
)
from agrojud.sources.datajud import DATAJUD_TJGO_ENDPOINT

OPERATIONAL_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_demo"
TEST_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_adapter_test"
API_KEY = "do-not-leak-api-key"


def make_settings(*, environment: str = "test", api_key: str | None = API_KEY) -> Settings:
    return Settings(
        environment=environment,  # type: ignore[arg-type]
        database_url=OPERATIONAL_URL,
        test_database_url=TEST_URL,
        datajud_api_key=SecretStr(api_key) if api_key is not None else None,
    )


def response_body(*, hits: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "hits": {
            "total": {"value": len(hits or []), "relation": "eq"},
            "hits": hits or [],
        }
    }


def data_hit(sort: list[object]) -> dict[str, object]:
    source_id = "TJGO_100_G1_123_00000010020248090001"
    return {
        "_id": source_id,
        "_source": {
            "id": source_id,
            "numeroProcesso": "00000010020248090001",
            "tribunal": "TJGO",
            "campoOpcional": {"versao": 3},
        },
        "sort": sort,
    }


def query() -> SourceQuery:
    return SourceQuery(class_codes=(1116,))


def test_posts_to_fixed_tjgo_endpoint_and_preserves_cursor_without_retry() -> None:
    requests: list[httpx.Request] = []
    bodies = [
        response_body(hits=[data_hit([1700000000000])]),
        response_body(hits=[data_hit([1700000000123])]),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == DATAJUD_TJGO_ENDPOINT
        assert request.headers["Authorization"] == f"APIKey {API_KEY}"
        return httpx.Response(200, json=bodies[len(requests) - 1])

    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(handler))
    try:
        first = adapter.fetch_page(query(), None, 100)
        second = adapter.fetch_page(query(), first.cursor_final, 100)
    finally:
        adapter.close()

    assert len(requests) == 2
    assert first.cursor_final == (1700000000000,)
    assert second.cursor_final == (1700000000123,)
    second_body = json.loads(requests[1].content)
    assert second_body["search_after"] == [1700000000000]
    assert second_body["size"] == 100
    assert second_body["query"]["bool"]["filter"][0] == {"match": {"tribunal": "TJGO"}}
    assert second_body["sort"] == [{"@timestamp": {"order": "asc"}}]


def test_query_compilation_uses_only_allowlisted_filters_and_half_open_dates() -> None:
    from datetime import date

    from agrojud.sources.contracts import build_datajud_payload

    request_body = build_datajud_payload(
        SourceQuery(
            filed_from=date(2026, 1, 1),
            filed_to=date(2026, 2, 1),
            class_codes=(1116, 100),
            subject_codes=(4968,),
            court_unit_code=13597,
            preset_id="rural",
            preset_version="3",
        ),
        None,
        25,
    )
    encoded = json.dumps(request_body)

    assert request_body["size"] == 25
    assert '"gte": "2026-01-01"' in encoded
    assert '"lt": "2026-02-01"' in encoded
    assert "classe.codigo" in encoded
    assert "assuntos.codigo" in encoded
    assert "orgaoJulgador.codigo" in encoded
    assert "script" not in encoded


def test_empty_200_is_not_a_source_error() -> None:
    adapter = DataJudSourceAdapter(
        make_settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_body())),
    )
    try:
        page = adapter.fetch_by_case_number("00000010020248090001", page_size=10)
    finally:
        adapter.close()

    assert page.is_empty
    assert page.total_value == 0


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, SourceErrorCode.VALIDATION),
        (401, SourceErrorCode.AUTHENTICATION),
        (403, SourceErrorCode.AUTHORIZATION),
        (429, SourceErrorCode.RATE_LIMIT),
        (503, SourceErrorCode.SOURCE_UNAVAILABLE),
    ],
)
def test_http_status_errors_are_typed_and_sanitized(
    status: int,
    code: SourceErrorCode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            headers={"Retry-After": "17"} if status == 429 else {},
            content=b"sensitive remote response body",
        )

    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(handler))
    caplog.set_level(logging.WARNING, logger="agrojud.sources.datajud")
    try:
        with pytest.raises(SourceError) as captured:
            adapter.fetch_page(query(), None, 10)
    finally:
        adapter.close()

    assert captured.value.code is code
    assert captured.value.status_code == status
    assert captured.value.retry_after == ("17" if status == 429 else None)
    assert calls == 1
    assert API_KEY not in caplog.text
    assert "sensitive remote response body" not in caplog.text
    assert "Authorization" not in caplog.text


def test_only_an_explicit_cursor_rejection_is_classified_as_cursor_invalid() -> None:
    explicit = DataJudSourceAdapter(
        make_settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={"error": {"reason": "search_after cursor is invalid"}},
            )
        ),
    )
    try:
        with pytest.raises(SourceError) as captured:
            explicit.fetch_page(query(), (1700000000000,), 10)
    finally:
        explicit.close()
    assert captured.value.code is SourceErrorCode.CURSOR_INVALID

    generic = DataJudSourceAdapter(
        make_settings(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(400, json={"error": {"reason": "invalid query"}})
        ),
    )
    try:
        with pytest.raises(SourceError) as captured:
            generic.fetch_page(query(), (1700000000000,), 10)
    finally:
        generic.close()
    assert captured.value.code is SourceErrorCode.VALIDATION


@pytest.mark.parametrize(
    "handler",
    [
        lambda _: httpx.Response(200, content=b"<html>not JSON</html>"),
        lambda _: httpx.Response(200, json={"hits": {"hits": "not a list"}}),
        lambda _: httpx.Response(200, json=response_body(hits=[{"_id": "x", "_source": {}}])),
    ],
)
def test_malformed_json_or_envelope_is_contract_error(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SourceError) as captured:
            adapter.fetch_page(query(), (1700000000000,), 10)
    finally:
        adapter.close()

    assert captured.value.code is SourceErrorCode.CONTRACT


def test_timeout_is_network_failure_and_does_not_retry(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout(f"timeout containing {API_KEY}")

    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(handler))
    caplog.set_level(logging.WARNING, logger="agrojud.sources.datajud")
    try:
        with pytest.raises(SourceError) as captured:
            adapter.fetch_page(query(), None, 10)
    finally:
        adapter.close()

    assert captured.value.code is SourceErrorCode.NETWORK
    assert calls == 1
    assert API_KEY not in caplog.text
    assert API_KEY not in str(captured.value)


def test_redirects_are_not_followed_and_validation_happens_before_http() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(307, headers={"Location": "https://example.invalid/"})

    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SourceError) as captured:
            adapter.fetch_page(query(), None, 101)
        assert captured.value.code is SourceErrorCode.VALIDATION
        assert calls == 0

        with pytest.raises(SourceError) as redirected:
            adapter.fetch_page(query(), None, 10)
    finally:
        adapter.close()

    assert redirected.value.code is SourceErrorCode.CONTRACT
    assert redirected.value.status_code == 307
    assert calls == 1


def test_exact_timeout_configuration() -> None:
    adapter = DataJudSourceAdapter(
        make_settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_body())),
    )
    try:
        timeout = adapter._client.timeout  # type: ignore[attr-defined]
        assert timeout.connect == 5.0
        assert timeout.read == 20.0
        assert timeout.write == 20.0
        assert timeout.pool == 5.0
    finally:
        adapter.close()
