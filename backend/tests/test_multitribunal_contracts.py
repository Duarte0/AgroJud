"""Unit coverage for allowlisted tribunal queries and the reviewed matrix gate."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

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

_ROOT = Path(__file__).parents[2]
_MATRIX = (
    _ROOT / "docs/evidence/datajud-multitribunal-2026-10-11-lote-01/"
    "matrix-merged-after-tjms-rerun-reviewed.json"
)
_MATRIX_SHA256 = "c9d43ac5879acf4fde6d2a99d3485b96dba73af54e06ebe4db746a8d3fb42b8d"
_PRESET_S5 = {
    "execucoes.amplas": {"TJGO", "TJMT", "TJMS", "TJPR", "TRF1"},
    "recuperacao_judicial.por_assunto": {"TJMT", "TJMS", "TJPR"},
    "recuperacao_judicial.por_classe": {"TJGO", "TJMT", "TJMS", "TJPR"},
    "rural.credito_contratos": {"TJGO", "TJMT", "TJMS", "TJPR"},
}


def _settings(
    *, enabled: bool = True, matrix: Path = _MATRIX, digest: str = _MATRIX_SHA256
) -> Settings:
    return Settings(
        environment="demo",
        database_url="postgresql+psycopg://test:test@127.0.0.1:55433/agrojud_demo",
        datajud_multitribunal_enabled=enabled,
        datajud_multitribunal_matrix_path=str(matrix),
        datajud_multitribunal_matrix_sha256=digest,
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
    page = parse_source_page(
        {"hits": {"hits": [raw]}},
        datetime(2026, 10, 8, tzinfo=UTC),
    )
    assert normalize_source_cover(page.hits[0], expected_tribunal="TJMT").tribunal == "TJMT"
    with pytest.raises(SourceError) as captured:
        normalize_source_cover(page.hits[0])
    assert captured.value.code is SourceErrorCode.VALIDATION
    assert captured.value.validation_code == "TRIBUNAL_INVALID"


def test_matrix_gate_enables_only_reviewed_tribunal_preset_pairs() -> None:
    assert hashlib.sha256(_MATRIX.read_bytes()).hexdigest() == _MATRIX_SHA256
    settings = _settings()
    direct = tribunal_availability(settings)
    assert direct.selection_enabled is True
    assert {item.tribunal for item in direct.tribunals if item.enabled} == {
        "TJGO",
        "TJMT",
        "TJMS",
        "TJPR",
        "TRF1",
    }

    for preset_id, enabled in _PRESET_S5.items():
        snapshot = tribunal_availability(settings, preset_id=preset_id)
        assert {item.tribunal for item in snapshot.tribunals if item.enabled} == enabled

    with pytest.raises(SourceError) as captured:
        require_tribunal_available(
            settings,
            "TJGO",
            preset_id="recuperacao_judicial.por_assunto",
        )
    assert captured.value.validation_code == "TRIBUNAL_CAPABILITY_UNAVAILABLE"
    assert require_tribunal_available(settings, "TRF1", preset_id="execucoes.amplas").enabled


def test_flag_and_promoted_hash_fail_closed(tmp_path: Path) -> None:
    disabled = tribunal_availability(_settings(enabled=False))
    assert disabled.selection_enabled is False
    assert not any(item.enabled for item in disabled.tribunals)

    tampered = tmp_path / "matrix-tampered.json"
    tampered.write_bytes(_MATRIX.read_bytes() + b" ")
    invalid = tribunal_availability(_settings(matrix=tampered))
    assert invalid.selection_enabled is False
    assert not any(item.enabled for item in invalid.tribunals)
    assert any("SHA-256" in reason for reason in invalid.global_reasons)


def test_unknown_preset_is_a_typed_validation_error() -> None:
    with pytest.raises(SourceError) as captured:
        tribunal_availability(_settings(), preset_id="not.a.current.preset")
    assert captured.value.code is SourceErrorCode.VALIDATION
    assert captured.value.validation_code == "PRESET_UNKNOWN"


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


def test_datajud_adapter_posts_to_the_allowlisted_dynamic_endpoint() -> None:
    seen_urls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
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
        query = build_query_by_case_number("0000001-00.2024.8.11.0001", tribunal="TJMT")
        assert adapter.fetch_page(query, None, 100).is_empty

    assert seen_urls == ["https://api-publica.datajud.cnj.jus.br/api_publica_tjmt/_search"]
