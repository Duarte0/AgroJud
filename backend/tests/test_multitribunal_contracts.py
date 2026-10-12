"""Contract coverage for the DataJud tribunal allowlist and adapter."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr

from agrojud.config import Settings
from agrojud.services.saved_searches import _normalized_filter_dict
from agrojud.sources.contracts import (
    SourceError,
    SourceErrorCode,
    SourceQuery,
    build_datajud_payload,
    build_query_by_case_number,
    normalize_source_cover,
    parse_source_page,
)
from agrojud.sources.datajud import DataJudSourceAdapter
from agrojud.sources.tribunal_availability import (
    require_tribunal_available,
    tribunal_availability,
)
from agrojud.sources.tribunals import TRIBUNAL_ALLOWLIST, canonicalize_tribunal, tribunal_endpoint


def _settings() -> Settings:
    return Settings(
        environment="demo",
        database_url="postgresql+psycopg://test:test@127.0.0.1:55433/agrojud_demo",
    )


def test_allowlist_maps_aliases_and_canonical_siglas_without_accepting_urls() -> None:
    assert len(TRIBUNAL_ALLOWLIST) == 33
    assert canonicalize_tribunal("tjmt") == "TJMT"
    assert tribunal_endpoint("TJMT").alias == "tjmt"
    assert tribunal_endpoint("trf1").endpoint_url.endswith("/api_publica_trf1/_search")
    with pytest.raises(ValueError):
        tribunal_endpoint("https://attacker.invalid/_search")


def test_query_payload_and_identity_are_bound_to_the_selected_tribunal() -> None:
    query = build_query_by_case_number("0000001-00.2024.8.11.0001", tribunal="tjmt")
    assert query.tribunal == "TJMT"
    payload = build_datajud_payload(query, None, 100)
    assert payload["query"]["bool"]["filter"][0] == {"match": {"tribunal": "TJMT"}}  # type: ignore[index]

    raw = {
        "_id": "TJMT_100_G1_123_00000010020248090001",
        "_source": {
            "id": "TJMT_100_G1_123_00000010020248090001",
            "numeroProcesso": "00000010020248090001",
            "tribunal": "TJMT",
        },
        "sort": [1735689600000, "TJMT_100_G1_123_00000010020248090001"],
    }
    page = parse_source_page({"hits": {"hits": [raw]}}, datetime(2026, 10, 8, tzinfo=UTC))
    assert normalize_source_cover(page.hits[0], expected_tribunal="TJMT").tribunal == "TJMT"
    with pytest.raises(SourceError) as captured:
        normalize_source_cover(page.hits[0])
    assert captured.value.code is SourceErrorCode.VALIDATION
    assert captured.value.validation_code == "TRIBUNAL_INVALID"


def test_allowlist_availability_does_not_depend_on_preset_or_matrix_evidence() -> None:
    settings = _settings()
    direct = tribunal_availability(settings)
    expected = {endpoint.canonical_tribunal for endpoint in TRIBUNAL_ALLOWLIST.values()}
    assert direct.selection_enabled is True
    assert {item.tribunal for item in direct.tribunals if item.enabled} == expected
    assert len(direct.tribunals) == 33
    assert direct.matrix_sha256 is None
    assert direct.global_reasons == ()
    assert all(item.s1_s2_state == "NOT_REQUIRED" for item in direct.tribunals)

    snapshot = tribunal_availability(settings, preset_id="not.a.current.preset")
    assert snapshot.selection_enabled is True
    assert {item.tribunal for item in snapshot.tribunals if item.enabled} == expected
    assert all(item.preset_state == "NOT_REQUIRED" for item in snapshot.tribunals)
    assert require_tribunal_available(settings, "TRF6", preset_id="not.a.current.preset").enabled


def test_allowlist_requirement_rejects_unknown_tribunals_before_enqueue() -> None:
    with pytest.raises(SourceError) as captured:
        require_tribunal_available(_settings(), "TJOUTRO")
    assert captured.value.code is SourceErrorCode.VALIDATION
    assert captured.value.validation_code == "TRIBUNAL_NOT_ALLOWLISTED"


def test_source_query_rejects_arbitrary_tribunal_values() -> None:
    with pytest.raises(SourceError) as captured:
        SourceQuery(tribunal="TJRANDOM", process_number="00000010020248090001")
    assert captured.value.code is SourceErrorCode.VALIDATION
    assert captured.value.validation_code == "TRIBUNAL_NOT_ALLOWLISTED"


def test_legacy_saved_search_filters_read_as_tjgo_without_rewriting_the_snapshot() -> None:
    legacy = {
        "filed_from": "2025-01-01",
        "filed_through": "2025-02-01",
        "page_size": 100,
        "hit_budget": 200,
    }
    normalized = _normalized_filter_dict(legacy)

    assert normalized["tribunals"] == ["TJGO"]
    assert "tribunals" not in legacy


def test_datajud_adapter_uses_the_same_backend_key_with_each_allowlisted_alias() -> None:
    seen_urls: list[str] = []
    seen_authorization: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        seen_authorization.append(request.headers.get("Authorization"))
        return httpx.Response(
            200,
            json={"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}},
        )

    settings = Settings(
        environment="test",
        test_database_url="postgresql+psycopg://test:test@127.0.0.1:55433/agrojud_test",
        datajud_api_key=SecretStr("synthetic-test-key"),
    )
    with DataJudSourceAdapter(settings, transport=httpx.MockTransport(respond)) as adapter:
        for tribunal in ("TJMT", "TRF6"):
            query = build_query_by_case_number("0000001-00.2024.8.11.0001", tribunal=tribunal)
            assert adapter.fetch_page(query, None, 100).is_empty

    assert seen_urls == [
        "https://api-publica.datajud.cnj.jus.br/api_publica_tjmt/_search",
        "https://api-publica.datajud.cnj.jus.br/api_publica_trf6/_search",
    ]
    assert seen_authorization == ["APIKey synthetic-test-key"] * 2
