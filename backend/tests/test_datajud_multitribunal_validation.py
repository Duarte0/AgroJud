"""Tests for the bounded and sanitized multitribunal DataJud validation tool."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr

import agrojud.sources.datajud_multitribunal_validation as multitribunal_module
from agrojud.config import Settings
from agrojud.sources.catalog import load_catalog
from agrojud.sources.contracts import SourceError, SourceErrorCode
from agrojud.sources.datajud_multitribunal_matrix import (
    main as matrix_main,
)
from agrojud.sources.datajud_multitribunal_matrix import (
    merge_batch_reports,
    review_coverage_matrix,
)
from agrojud.sources.datajud_multitribunal_validation import (
    ALLOWLIST_VERSION,
    DATAJUD_BASE_URL,
    ENDPOINT_CATALOG_URL,
    MAX_PAGE_SIZE,
    MAX_S1_S2_REQUESTS_PER_TRIBUNAL,
    MAX_S5_REQUESTS_PER_PAIR,
    MAX_TRIBUNALS_PER_BATCH,
    MAX_WINDOW_DAYS,
    PRESET_IDS,
    S5_REQUEST_SPACING_SECONDS,
    TPU_REFERENCE_URL,
    TRIBUNAL_ALLOWLIST,
    DataJudMultiTribunalClient,
    DataJudMultiTribunalProbe,
    EvidenceState,
    ProbeQuery,
    TPUApplicability,
    _write_batch_reports,
    applicability_template,
    build_probe_payload,
    current_discovery_presets,
    get_endpoint,
    main,
    parse_applicability_document,
    validate_alias_batch,
    validate_date_window,
)

API_KEY = "synthetic-probe-key-never-report"
DATABASE_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_real"
FILED_FROM = date(2026, 1, 5)
FILED_TO = date(2026, 1, 12)
CASE_A = "10000000000000000001"
CASE_B = "10000000000000000002"
CASE_C = "10000000000000000003"
SOURCE_A = "TJDOC_PRIVATE_A"
SOURCE_B = "TJDOC_PRIVATE_B"
SOURCE_C = "TJDOC_PRIVATE_C"
PRIVATE_PARTY = "Parte Sintetica Privada"


def make_settings(*, environment: str = "real", api_key: str | None = API_KEY) -> Settings:
    return Settings(
        environment=environment,  # type: ignore[arg-type]
        database_url=DATABASE_URL,
        datajud_api_key=SecretStr(api_key) if api_key is not None else None,
    )


def hit(
    alias: str,
    case_number: str,
    source_id: str,
    sort_values: list[object],
    *,
    filed_at: str = "20260106123000",
    class_codes: list[int] | None = None,
    subject_codes: list[int] | None = None,
    movement_codes: list[int] | None = None,
    tribunal: str | None = None,
) -> dict[str, object]:
    return {
        "_id": source_id,
        "_source": {
            "id": source_id,
            "numeroProcesso": case_number,
            "tribunal": tribunal or alias.upper(),
            "dataAjuizamento": filed_at,
            "classe": {"codigo": (class_codes or [159])[0]},
            "assuntos": [{"codigo": code} for code in (subject_codes or [10501])],
            "movimentos": [{"codigo": code} for code in (movement_codes or [])],
            "nomeParte": PRIVATE_PARTY,
            "textoLivre": "payload privado não deve ir ao relatório",
        },
        "sort": sort_values,
    }


def response_body(hits: list[dict[str, object]]) -> dict[str, object]:
    last_sort = hits[-1]["sort"] if hits else None
    return {
        "hits": {
            "total": {"value": len(hits), "relation": "eq"},
            "hits": hits,
        },
        "cursor_debug": last_sort,
    }


def _request_filters(body: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return cast(list[Mapping[str, Any]], body["query"]["bool"]["filter"])


def _requested_codes(body: Mapping[str, Any]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for clause in _request_filters(body):
        terms = clause.get("terms")
        if isinstance(terms, Mapping):
            for key, values in terms.items():
                if isinstance(values, list):
                    result[str(key)] = [int(value) for value in values]
    return result


def standard_handler(requests: list[httpx.Request]) -> httpx.MockTransport:
    """Provide deterministic pages for S1/S2 and each applicable S5 preset."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = cast(dict[str, Any], json.loads(request.content))
        alias = request.url.path.split("api_publica_", 1)[1].split("/", 1)[0]
        sort_fields = [next(iter(term)) for term in body["sort"]]
        filters = _request_filters(body)
        exact_number = next(
            (
                str(clause["match"]["numeroProcesso"])
                for clause in filters
                if "match" in clause and "numeroProcesso" in clause["match"]
            ),
            None,
        )
        requested_codes = _requested_codes(body)
        if requested_codes:
            classes = requested_codes.get("classe.codigo", [159])
            subjects = requested_codes.get("assuntos.codigo", [10501])
            movements = requested_codes.get("movimentos.codigo", [])
            sample = [
                hit(
                    alias,
                    f"1000000000000000000{index + 1}",
                    f"S5_PRIVATE_{alias}_{index}",
                    [100 + index]
                    if len(sort_fields) == 1
                    else [100 + index, f"S5_PRIVATE_{index}"],
                    class_codes=classes,
                    subject_codes=subjects,
                    movement_codes=movements,
                )
                for index in range(3)
            ]
            return httpx.Response(200, json=response_body(sample))
        if exact_number is not None:
            sort = [100] if len(sort_fields) == 1 else [100, SOURCE_A]
            return httpx.Response(
                200,
                json=response_body([hit(alias, exact_number, SOURCE_A, sort)]),
            )
        if "search_after" in body:
            if len(sort_fields) == 2:
                return httpx.Response(
                    200,
                    json=response_body([hit(alias, CASE_C, SOURCE_C, [300, SOURCE_C])]),
                )
            return httpx.Response(
                200,
                json=response_body([hit(alias, CASE_C, SOURCE_C, [300])]),
            )
        if len(sort_fields) == 2:
            return httpx.Response(
                200,
                json=response_body(
                    [
                        hit(alias, CASE_A, SOURCE_A, [100, SOURCE_A]),
                        hit(alias, CASE_B, SOURCE_B, [200, SOURCE_B]),
                    ]
                ),
            )
        return httpx.Response(
            200,
            json=response_body(
                [hit(alias, CASE_A, SOURCE_A, [100]), hit(alias, CASE_B, SOURCE_B, [200])]
            ),
        )

    return httpx.MockTransport(handler)


def make_applicability(
    state: str = EvidenceState.APPLICABLE,
    *,
    preset_ids: tuple[str, ...] = PRESET_IDS,
) -> dict[tuple[str, str], TPUApplicability]:
    return {
        ("tjgo", preset_id): TPUApplicability(
            state=state,
            tpu_version=load_catalog().tpu_version,
            reference_url=TPU_REFERENCE_URL,
            rationale=f"Revisão sintética de competência para {preset_id}.",
        )
        for preset_id in preset_ids
    }


def test_versioned_allowlist_has_only_the_33_requested_tj_and_trf_aliases() -> None:
    expected = {
        "tjac",
        "tjal",
        "tjam",
        "tjap",
        "tjba",
        "tjce",
        "tjdft",
        "tjes",
        "tjgo",
        "tjma",
        "tjmg",
        "tjms",
        "tjmt",
        "tjpa",
        "tjpb",
        "tjpe",
        "tjpi",
        "tjpr",
        "tjrj",
        "tjrn",
        "tjro",
        "tjrr",
        "tjrs",
        "tjsc",
        "tjse",
        "tjsp",
        "tjto",
        "trf1",
        "trf2",
        "trf3",
        "trf4",
        "trf5",
        "trf6",
    }
    assert ALLOWLIST_VERSION == "2026-10-11"
    assert len(TRIBUNAL_ALLOWLIST) == 33
    assert set(TRIBUNAL_ALLOWLIST) == expected
    assert TRIBUNAL_ALLOWLIST["tjdft"].canonical_tribunal == "TJDFT"
    assert TRIBUNAL_ALLOWLIST["trf6"].endpoint_url == (
        f"{DATAJUD_BASE_URL}/api_publica_trf6/_search"
    )
    assert ENDPOINT_CATALOG_URL.startswith("https://datajud-wiki.cnj.jus.br/")


@pytest.mark.parametrize(
    "aliases",
    [[], ["tjgo"] * 6, ["tjgo", "tjgo"], ["unknown"], ["https://example.test"]],
)
def test_invalid_alias_batches_fail_before_a_client_can_be_used(aliases: list[str]) -> None:
    with pytest.raises(ValueError):
        validate_alias_batch(aliases)


def test_valid_explicit_batches_are_limited_to_five() -> None:
    aliases = ("tjac", "tjal", "tjam", "tjap", "tjba")
    assert validate_alias_batch(aliases) == aliases
    assert MAX_TRIBUNALS_PER_BATCH == 5
    with pytest.raises(ValueError, match="um a cinco"):
        validate_alias_batch(["tjac", "tjal", "tjam", "tjap", "tjba", "tjce"])


def test_endpoint_resolution_and_payload_use_only_the_allowlist() -> None:
    endpoint = get_endpoint("tjes")
    payload = build_probe_payload(
        endpoint,
        ProbeQuery(filed_from=FILED_FROM, filed_to=FILED_TO),
        None,
        100,
    )
    assert endpoint.endpoint_url == f"{DATAJUD_BASE_URL}/api_publica_tjes/_search"
    filters = _request_filters(payload)
    assert filters[0] == {"match": {"tribunal": "TJES"}}
    assert payload["size"] == 100
    with pytest.raises(ValueError, match="fora da allowlist"):
        get_endpoint("https://api-publica.datajud.cnj.jus.br/api_publica_tjgo/_search")  # type: ignore[arg-type]


def test_client_requires_real_environment_and_a_nonempty_key() -> None:
    with pytest.raises(SourceError, match="AGROJUD_ENV=real"):
        DataJudMultiTribunalClient(make_settings(environment="demo"))
    with pytest.raises(SourceError, match="DATAJUD_API_KEY"):
        DataJudMultiTribunalClient(make_settings(api_key=None))


def test_s1_s2_observes_six_calls_and_sanitizes_report_without_database_or_activation() -> None:
    requests: list[httpx.Request] = []
    transport = standard_handler(requests)
    client = DataJudMultiTribunalClient(make_settings(), transport=transport)
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo"], filed_from=FILED_FROM, filed_to=FILED_TO, client=client, terms_reviewed=True
        ).run()
    finally:
        client.close()

    report = cast(list[dict[str, object]], result["tribunal_reports"])[0]
    s1 = cast(dict[str, object], report["s1_s2"])
    assert len(requests) == s1["requests_made"] == MAX_S1_S2_REQUESTS_PER_TRIBUNAL == 6
    assert all(request.url.host == "api-publica.datajud.cnj.jus.br" for request in requests)
    assert all(json.loads(request.content)["size"] <= MAX_PAGE_SIZE for request in requests)
    assert all(request.headers["Authorization"] == f"APIKey {API_KEY}" for request in requests)
    assert all("_search" in str(request.url) for request in requests)
    assert cast(list[dict[str, object]], s1["requests"])[0]["response"]["hit_count"] == 2
    capabilities = cast(dict[str, dict[str, object]], s1["capabilities"])
    assert capabilities["http_access"]["state"] == EvidenceState.VALIDATED
    assert capabilities["envelope"]["state"] == EvidenceState.VALIDATED
    assert capabilities["essential_fields"]["state"] == EvidenceState.VALIDATED
    assert capabilities["tribunal_identity"]["state"] == EvidenceState.VALIDATED
    assert capabilities["date_filter"]["state"] == EvidenceState.VALIDATED
    assert capabilities["query_by_case_number"]["state"] == EvidenceState.VALIDATED
    assert capabilities["source_identity"]["state"] == EvidenceState.VALIDATED
    assert capabilities["sort_timestamp"]["state"] == EvidenceState.VALIDATED
    assert capabilities["compound_sort"]["state"] == EvidenceState.VALIDATED
    assert capabilities["pagination"]["state"] == EvidenceState.VALIDATED
    assert report["runtime_activation_changed"] is False
    assert report["request_budget"]["writes_to_product_database"] is False

    serialized = json.dumps(result, ensure_ascii=False)
    for secret in (API_KEY, CASE_A, SOURCE_A, SOURCE_B, SOURCE_C, PRIVATE_PARTY):
        assert secret not in serialized
    assert "payload privado" not in serialized
    assert "cursor_debug" not in serialized
    assert "<numeroProcesso-observado-redigido>" in serialized
    assert "<cursor-redigido>" in serialized


def test_probe_requires_terms_review_even_when_real_key_and_client_are_available() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=response_body([]))

    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo"], filed_from=FILED_FROM, filed_to=FILED_TO, client=client
        ).run()
    finally:
        client.close()
    report = cast(list[dict[str, object]], result["tribunal_reports"])[0]
    assert calls == []
    assert report["requests_made"] == 0
    assert report["terms_review"]["operator_confirmed_before_probe"] is False
    assert (
        "confirm" in report["s1_s2"]["capabilities"]["http_access"]["evidence"]["reason"].casefold()
    )


def test_operator_rationale_with_private_data_is_never_copied_into_reports() -> None:
    applicability = {
        ("tjgo", PRESET_IDS[0]): TPUApplicability(
            EvidenceState.INCONCLUSIVE,
            load_catalog().tpu_version,
            TPU_REFERENCE_URL,
            f"Revisar amostra de {PRIVATE_PARTY} relacionada ao CNJ {CASE_A}.",
        )
    }
    report = DataJudMultiTribunalProbe(
        ["tjgo"],
        filed_from=FILED_FROM,
        filed_to=FILED_TO,
        applicability=applicability,
    ).run()
    serialized = json.dumps(report, ensure_ascii=False)
    assert CASE_A not in serialized
    assert PRIVATE_PARTY not in serialized
    assert "Revisar amostra de" not in serialized
    assert "Evidência TPU/competência insuficiente" in serialized


@pytest.mark.parametrize("status", [401, 429])
def test_401_and_429_stop_the_batch_and_mark_remaining_tribunals_not_run(status: int) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, content=b"private remote body")

    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjac", "tjal"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    reports = cast(list[dict[str, object]], result["tribunal_reports"])
    assert len(requests) == 1
    assert reports[0]["requests_made"] == 1
    assert reports[1]["s1_s2"]["aggregate_state"] == EvidenceState.NOT_RUN
    assert reports[1]["stop_reason"] is not None
    summary = cast(dict[str, object], result["summary"])
    assert summary["requests_made"] == 1
    assert summary["failures_and_not_run"]
    assert "private remote body" not in json.dumps(result)


def test_403_5xx_timeout_and_invalid_envelope_are_isolated_and_never_empty_success() -> None:
    scenarios: list[tuple[int | str, str]] = [
        (403, SourceErrorCode.AUTHORIZATION.value),
        (503, SourceErrorCode.SOURCE_UNAVAILABLE.value),
        ("timeout", SourceErrorCode.NETWORK.value),
        (200, SourceErrorCode.CONTRACT.value),
    ]
    for outcome, expected_code in scenarios:
        calls: list[httpx.Request] = []

        def handler(
            request: httpx.Request,
            *,
            active_outcome: int | str = outcome,
            active_calls: list[httpx.Request] = calls,
        ) -> httpx.Response:
            active_calls.append(request)
            if active_outcome == "timeout":
                raise httpx.ReadTimeout("private timeout detail")
            if active_outcome == 200:
                return httpx.Response(200, json={"hits": {"hits": "invalid"}})
            return httpx.Response(int(active_outcome), content=b"private response detail")

        client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
        try:
            result = DataJudMultiTribunalProbe(
                ["tjac"],
                filed_from=FILED_FROM,
                filed_to=FILED_TO,
                client=client,
                terms_reviewed=True,
            ).run()
        finally:
            client.close()
        report = cast(list[dict[str, object]], result["tribunal_reports"])[0]
        s1 = cast(dict[str, object], report["s1_s2"])
        first_request = cast(list[dict[str, object]], s1["requests"])[0]
        assert len(calls) == s1["requests_made"] == 1
        assert first_request["error_code"] == expected_code
        assert "private response detail" not in json.dumps(result)
        assert "private timeout detail" not in json.dumps(result)
        assert "records=0" not in json.dumps(result)
        if outcome == 200:
            assert s1["capabilities"]["envelope"]["state"] == EvidenceState.INCOMPATIBLE
        else:
            assert s1["capabilities"]["envelope"]["state"] == EvidenceState.INCONCLUSIVE


def test_failure_on_one_endpoint_preserves_the_next_endpoint_report() -> None:
    requests: list[httpx.Request] = []
    good = standard_handler(requests)

    def handler(request: httpx.Request) -> httpx.Response:
        if "api_publica_tjac" in str(request.url):
            requests.append(request)
            return httpx.Response(500, content=b"private server body")
        return good.handle_request(request)

    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjac", "tjal"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    reports = cast(list[dict[str, object]], result["tribunal_reports"])
    assert reports[0]["s1_s2"]["aggregate_state"] == EvidenceState.INCONCLUSIVE
    assert reports[0]["s1_s2"]["requests_made"] == 1
    assert reports[1]["s1_s2"]["aggregate_state"] == EvidenceState.VALIDATED
    assert reports[1]["s1_s2"]["requests_made"] == 6
    assert result["summary"]["requests_made"] == 7
    assert "private server body" not in json.dumps(result)


def test_403_isolated_to_its_endpoint_and_does_not_stop_the_rest_of_the_batch() -> None:
    requests: list[httpx.Request] = []
    good = standard_handler(requests)

    def handler(request: httpx.Request) -> httpx.Response:
        if "api_publica_tjac" in str(request.url):
            requests.append(request)
            return httpx.Response(403, content=b"private authorization body")
        return good.handle_request(request)

    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjac", "tjal"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    reports = cast(list[dict[str, object]], result["tribunal_reports"])
    assert reports[0]["s1_s2"]["aggregate_state"] == EvidenceState.INCOMPATIBLE
    assert reports[1]["s1_s2"]["aggregate_state"] == EvidenceState.VALIDATED
    assert "private authorization body" not in json.dumps(result)


@pytest.mark.parametrize(
    ("bad_field", "capability"),
    [
        ("numeroProcesso", "essential_fields"),
        ("tribunal", "tribunal_identity"),
        ("dataAjuizamento", "essential_fields"),
        ("id", "source_identity"),
    ],
)
def test_invalid_essential_fields_or_wrong_canonical_tribunal_are_not_approved(
    bad_field: str,
    capability: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            item = hit("tjac", CASE_A, SOURCE_A, [100])
            source = cast(dict[str, object], item["_source"])
            if bad_field == "numeroProcesso":
                source[bad_field] = "123"
            elif bad_field == "tribunal":
                source[bad_field] = "TJGO"
            elif bad_field == "dataAjuizamento":
                source[bad_field] = "not-a-date"
            else:
                source[bad_field] = "different-source-id"
            return httpx.Response(200, json=response_body([item]))
        return httpx.Response(500)

    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjac"], filed_from=FILED_FROM, filed_to=FILED_TO, client=client, terms_reviewed=True
        ).run()
    finally:
        client.close()
    s1 = cast(
        dict[str, object], cast(list[dict[str, object]], result["tribunal_reports"])[0]["s1_s2"]
    )
    capabilities = cast(dict[str, dict[str, object]], s1["capabilities"])
    assert capabilities[capability]["state"] == EvidenceState.INCOMPATIBLE


def test_rejected_compound_sort_is_incompatible_and_does_not_retry() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = cast(dict[str, Any], json.loads(request.content))
        fields = [next(iter(term)) for term in body["sort"]]
        if len(fields) == 2:
            return httpx.Response(400, content=b"private unsupported sort detail")
        if "search_after" in body:
            return httpx.Response(200, json=response_body([hit("tjac", CASE_C, SOURCE_C, [300])]))
        return httpx.Response(
            200,
            json=response_body(
                [hit("tjac", CASE_A, SOURCE_A, [100]), hit("tjac", CASE_B, SOURCE_B, [200])]
            ),
        )

    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjac"], filed_from=FILED_FROM, filed_to=FILED_TO, client=client, terms_reviewed=True
        ).run()
    finally:
        client.close()
    s1 = cast(
        dict[str, object], cast(list[dict[str, object]], result["tribunal_reports"])[0]["s1_s2"]
    )
    assert len(requests) == 5
    assert s1["capabilities"]["compound_sort"]["state"] == EvidenceState.INCOMPATIBLE
    assert s1["capabilities"]["pagination"]["state"] == EvidenceState.INCONCLUSIVE
    assert "private unsupported sort detail" not in json.dumps(result)


@pytest.mark.parametrize("pagination_fault", ["repeated_cursor", "overlap"])
def test_pagination_rejects_repeated_cursor_or_cross_page_duplicate(
    pagination_fault: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = cast(dict[str, Any], json.loads(request.content))
        filters = _request_filters(body)
        exact = any("numeroProcesso" in clause.get("match", {}) for clause in filters)
        if exact:
            return httpx.Response(
                200,
                json=response_body([hit("tjac", CASE_A, SOURCE_A, [100])]),
            )
        fields = [next(iter(term)) for term in body["sort"]]
        if len(fields) == 2:
            if "search_after" in body:
                if pagination_fault == "repeated_cursor":
                    next_hit = hit("tjac", CASE_C, SOURCE_C, [200, SOURCE_B])
                else:
                    next_hit = hit("tjac", CASE_C, SOURCE_B, [300, SOURCE_B])
                return httpx.Response(200, json=response_body([next_hit]))
            return httpx.Response(
                200,
                json=response_body(
                    [
                        hit("tjac", CASE_A, SOURCE_A, [100, SOURCE_A]),
                        hit("tjac", CASE_B, SOURCE_B, [200, SOURCE_B]),
                    ]
                ),
            )
        if "search_after" in body:
            return httpx.Response(
                200,
                json=response_body([hit("tjac", CASE_C, SOURCE_C, [300])]),
            )
        return httpx.Response(
            200,
            json=response_body(
                [hit("tjac", CASE_A, SOURCE_A, [100]), hit("tjac", CASE_B, SOURCE_B, [200])]
            ),
        )

    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjac"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    s1 = cast(
        dict[str, object], cast(list[dict[str, object]], result["tribunal_reports"])[0]["s1_s2"]
    )
    pagination = s1["capabilities"]["pagination"]
    assert pagination["state"] == EvidenceState.INCOMPATIBLE
    if pagination_fault == "repeated_cursor":
        assert pagination["evidence"]["cursor_advanced"] is False
    else:
        assert pagination["evidence"]["overlap_by_source_document_id"] == 1
        assert pagination["evidence"]["duplicate_ids_within_or_across_pages"] == 1


def test_pagination_remains_inconclusive_without_two_nonempty_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = cast(dict[str, Any], json.loads(request.content))
        exact = any(
            "numeroProcesso" in clause.get("match", {}) for clause in _request_filters(body)
        )
        if exact:
            return httpx.Response(200, json=response_body([hit("tjac", CASE_A, SOURCE_A, [100])]))
        fields = [next(iter(term)) for term in body["sort"]]
        if len(fields) == 2 and "search_after" in body:
            return httpx.Response(200, json=response_body([]))
        if len(fields) == 2:
            return httpx.Response(
                200,
                json=response_body(
                    [
                        hit("tjac", CASE_A, SOURCE_A, [100, SOURCE_A]),
                        hit("tjac", CASE_B, SOURCE_B, [200, SOURCE_B]),
                    ]
                ),
            )
        if "search_after" in body:
            return httpx.Response(200, json=response_body([hit("tjac", CASE_C, SOURCE_C, [300])]))
        return httpx.Response(
            200,
            json=response_body(
                [hit("tjac", CASE_A, SOURCE_A, [100]), hit("tjac", CASE_B, SOURCE_B, [200])]
            ),
        )

    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjac"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    s1 = cast(
        dict[str, object], cast(list[dict[str, object]], result["tribunal_reports"])[0]["s1_s2"]
    )
    assert s1["capabilities"]["pagination"]["state"] == EvidenceState.INCONCLUSIVE


def test_empty_page_is_not_a_validated_filter_or_successful_absence() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=response_body([]))

    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo"], filed_from=FILED_FROM, filed_to=FILED_TO, client=client, terms_reviewed=True
        ).run()
    finally:
        client.close()
    s1 = cast(
        dict[str, object], cast(list[dict[str, object]], result["tribunal_reports"])[0]["s1_s2"]
    )
    assert s1["capabilities"]["envelope"]["state"] == EvidenceState.VALIDATED
    assert s1["capabilities"]["date_filter"]["state"] == EvidenceState.INCONCLUSIVE
    assert s1["capabilities"]["query_by_case_number"]["state"] == EvidenceState.INCONCLUSIVE
    assert len(calls) == 2
    assert "records=0" not in json.dumps(result)


def test_blank_applicability_template_is_inconclusive_and_only_has_four_presets() -> None:
    template = applicability_template(["tjgo"])
    parsed = parse_applicability_document(template)
    assert len(parsed) == 4
    assert set(preset for _alias, preset in parsed) == set(PRESET_IDS)
    assert all(value.state == EvidenceState.INCONCLUSIVE for value in parsed.values())
    assert set(item.id for item in current_discovery_presets()) == set(PRESET_IDS)


def test_applicability_requires_official_current_tpu_evidence_for_terminal_states() -> None:
    base = cast(dict[str, Any], applicability_template(["tjgo"]))
    tribunal = cast(dict[str, Any], base["tribunals"])["tjgo"]
    tribunal["rural.credito_contratos"] = {
        "state": "APPLICABLE",
        "tpu_version": load_catalog().tpu_version,
        "reference_url": TPU_REFERENCE_URL,
        "rationale": "Competência compatível segundo evidência TPU.",
    }
    parsed = parse_applicability_document(base)
    assert parsed[("tjgo", "rural.credito_contratos")].state == "APPLICABLE"

    tribunal["rural.credito_contratos"]["reference_url"] = "https://example.test/tpu"
    with pytest.raises(ValueError, match="domínio oficial"):
        parse_applicability_document(base)

    tribunal["rural.credito_contratos"]["reference_url"] = TPU_REFERENCE_URL
    tribunal["rural.credito_contratos"]["tpu_version"] = "older-version"
    with pytest.raises(ValueError, match="versão TPU"):
        parse_applicability_document(base)

    tribunal["rural.credito_contratos"] = {
        "state": "NOT_APPLICABLE",
        "tpu_version": None,
        "reference_url": None,
        "rationale": "Sem evidência explícita de exclusão.",
    }
    with pytest.raises(ValueError, match="versão TPU e referência oficial"):
        parse_applicability_document(base)


def test_not_applicable_and_inconclusive_pairs_make_zero_s5_calls() -> None:
    applicability = {
        ("tjgo", PRESET_IDS[0]): TPUApplicability(
            EvidenceState.NOT_APPLICABLE,
            load_catalog().tpu_version,
            TPU_REFERENCE_URL,
            "A competência consultada exclui o preset.",
        ),
        ("tjgo", PRESET_IDS[1]): TPUApplicability(
            EvidenceState.INCONCLUSIVE,
            load_catalog().tpu_version,
            TPU_REFERENCE_URL,
            "A competência requer revisão adicional.",
        ),
    }
    requests: list[httpx.Request] = []
    client = DataJudMultiTribunalClient(make_settings(), transport=standard_handler(requests))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            applicability=applicability,
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    report = cast(list[dict[str, object]], result["tribunal_reports"])[0]
    pairs = {pair["preset_id"]: pair for pair in report["s5_pairs"]}
    assert pairs[PRESET_IDS[0]]["pair_state"] == EvidenceState.NOT_APPLICABLE
    assert pairs[PRESET_IDS[1]]["pair_state"] == EvidenceState.INCONCLUSIVE
    assert all(pairs[preset]["requests_made"] == 0 for preset in PRESET_IDS)
    assert len(requests) == MAX_S1_S2_REQUESTS_PER_TRIBUNAL


def test_s5_uses_current_preset_codes_and_validates_three_distinct_documents() -> None:
    requests: list[httpx.Request] = []
    client = DataJudMultiTribunalClient(make_settings(), transport=standard_handler(requests))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            applicability=make_applicability(),
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    report = cast(list[dict[str, object]], result["tribunal_reports"])[0]
    pairs = cast(list[dict[str, object]], report["s5_pairs"])
    assert len(pairs) == 4
    assert all(pair["requests_made"] == 1 for pair in pairs)
    assert all(pair["query_status"]["state"] == EvidenceState.VALIDATED for pair in pairs)
    assert all(pair["sample_status"]["state"] == EvidenceState.VALIDATED for pair in pairs)
    assert all(
        pair["sample_status"]["evidence"]["distinct_matching_documents"] == 3 for pair in pairs
    )
    assert all(pair["preset_version"] == "1.0.0" for pair in pairs)
    assert all(pair["applicability"]["tpu_version"] == load_catalog().tpu_version for pair in pairs)
    serialized_requests = json.dumps([pair["requests"] for pair in pairs])
    assert all(number not in serialized_requests for number in (CASE_A, CASE_B, CASE_C))


@pytest.mark.parametrize(
    ("sample_count", "expected_query", "expected_sample"),
    [
        (0, EvidenceState.INCONCLUSIVE, EvidenceState.INCONCLUSIVE),
        (1, EvidenceState.VALIDATED, EvidenceState.INCONCLUSIVE),
        (2, EvidenceState.VALIDATED, EvidenceState.INCONCLUSIVE),
    ],
)
def test_s5_zero_or_one_two_samples_never_approve_sample(
    sample_count: int,
    expected_query: str,
    expected_sample: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = cast(dict[str, Any], json.loads(request.content))
        codes = _requested_codes(body)
        if not codes:
            return httpx.Response(500)
        classes = codes.get("classe.codigo", [159])
        subjects = codes.get("assuntos.codigo", [10501])
        values = [
            hit(
                "tjgo",
                f"1000000000000000000{index + 1}",
                f"S5_ID_{index}",
                [100 + index],
                class_codes=classes,
                subject_codes=subjects,
            )
            for index in range(sample_count)
        ]
        return httpx.Response(200, json=response_body(values))

    applicability = make_applicability(preset_ids=(PRESET_IDS[0],))
    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            applicability=applicability,
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    report = cast(list[dict[str, object]], result["tribunal_reports"])[0]
    pair = next(pair for pair in report["s5_pairs"] if pair["preset_id"] == PRESET_IDS[0])
    assert pair["query_status"]["state"] == expected_query
    assert pair["sample_status"]["state"] == expected_sample
    assert pair["requests_made"] == 1


def test_s5_code_mismatch_is_incompatible_not_an_empty_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = cast(dict[str, Any], json.loads(request.content))
        codes = _requested_codes(body)
        if not codes:
            return httpx.Response(500)
        return httpx.Response(
            200,
            json=response_body(
                [
                    hit(
                        "tjgo",
                        CASE_A,
                        SOURCE_A,
                        [100],
                        subject_codes=[999999],
                    )
                ]
            ),
        )

    applicability = make_applicability(preset_ids=(PRESET_IDS[0],))
    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            applicability=applicability,
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    report = cast(list[dict[str, object]], result["tribunal_reports"])[0]
    pair = next(pair for pair in report["s5_pairs"] if pair["preset_id"] == PRESET_IDS[0])
    assert pair["query_status"]["state"] == EvidenceState.INCOMPATIBLE
    assert pair["sample_status"]["state"] == EvidenceState.INCOMPATIBLE


def test_s5_two_call_budget_and_one_second_spacing_are_per_pair() -> None:
    requests: list[httpx.Request] = []
    sleep_calls: list[float] = []
    s5_page = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal s5_page
        requests.append(request)
        body = cast(dict[str, Any], json.loads(request.content))
        if not _requested_codes(body):
            return httpx.Response(500)
        s5_page += 1
        codes = _requested_codes(body)
        classes = codes.get("classe.codigo", [159])
        subjects = codes.get("assuntos.codigo", [10501])
        if s5_page == 1:
            duplicates = [
                hit(
                    "tjgo",
                    CASE_A,
                    "DUPLICATE_S5_ID",
                    [100 + index],
                    class_codes=classes,
                    subject_codes=subjects,
                )
                for index in range(MAX_PAGE_SIZE)
            ]
            return httpx.Response(200, json=response_body(duplicates))
        second = [
            hit(
                "tjgo",
                CASE_B if index == 0 else CASE_C,
                "S5_SECOND_DUPLICATE",
                [500 + index],
                class_codes=classes,
                subject_codes=subjects,
            )
            for index in range(2)
        ]
        return httpx.Response(200, json=response_body(second))

    applicability = make_applicability(preset_ids=(PRESET_IDS[0],))
    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            applicability=applicability,
            client=client,
            terms_reviewed=True,
            sleep=sleep_calls.append,
        ).run()
    finally:
        client.close()
    report = cast(list[dict[str, object]], result["tribunal_reports"])[0]
    pair = next(pair for pair in report["s5_pairs"] if pair["preset_id"] == PRESET_IDS[0])
    assert pair["requests_made"] == MAX_S5_REQUESTS_PER_PAIR == 2
    assert pair["sample_status"]["state"] == EvidenceState.INCONCLUSIVE
    assert pair["sample_status"]["evidence"]["distinct_matching_documents"] == 2
    assert sleep_calls == [S5_REQUEST_SPACING_SECONDS]
    assert all(json.loads(request.content)["size"] <= MAX_PAGE_SIZE for request in requests)
    assert json.loads(requests[-1].content)["search_after"] == [199]
    assert MAX_WINDOW_DAYS == 31


def test_s5_incompatible_codes_and_429_do_not_retry_or_continue_batch() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = cast(dict[str, Any], json.loads(request.content))
        if _requested_codes(body):
            return httpx.Response(429, headers={"Retry-After": "30"})
        return httpx.Response(500)

    applicability = make_applicability(preset_ids=(PRESET_IDS[0],))
    client = DataJudMultiTribunalClient(make_settings(), transport=httpx.MockTransport(handler))
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo", "tjal"],
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            applicability=applicability,
            client=client,
            terms_reviewed=True,
        ).run()
    finally:
        client.close()
    reports = cast(list[dict[str, object]], result["tribunal_reports"])
    assert len(calls) == 2  # one 500 on TJGO, then one 429 on its applicable S5 pair
    assert reports[0]["s5_pairs"][0]["requests_made"] == 1
    assert reports[0]["s5_pairs"][0]["query_status"]["state"] == EvidenceState.INCONCLUSIVE
    assert reports[1]["s1_s2"]["aggregate_state"] == EvidenceState.NOT_RUN
    summary = cast(dict[str, object], result["summary"])
    assert summary["failures_and_not_run"][0]["failed_s5_requests"][0]["http_status"] == 429
    assert "Retry-After" not in json.dumps(result)


def test_31_day_window_is_allowed_and_32_days_is_rejected_before_http() -> None:
    validate_date_window(date(2026, 1, 1), date(2026, 2, 1))
    with pytest.raises(ValueError, match="31 dias"):
        validate_date_window(date(2026, 1, 1), date(2026, 2, 2))


def test_manual_date_window_is_required_and_is_never_selected_automatically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGROJUD_ENV", "real")
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("DATAJUD_API_KEY", API_KEY)
    with pytest.raises(SystemExit):
        main(["tjgo", "--output-dir", str(tmp_path)])


def test_batch_matrix_lists_all_33_aliases_and_four_pairs_without_national_claim() -> None:
    client = DataJudMultiTribunalClient(
        make_settings(), transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    )
    try:
        result = DataJudMultiTribunalProbe(
            ["tjgo"], filed_from=FILED_FROM, filed_to=FILED_TO, client=client, terms_reviewed=True
        ).run()
    finally:
        client.close()
    summary = cast(dict[str, object], result["summary"])
    matrix = cast(list[dict[str, object]], summary["coverage_matrix"])
    assert len(matrix) == len(TRIBUNAL_ALLOWLIST) == 33
    tjgo = next(row for row in matrix if row["alias"] == "tjgo")
    assert set(tjgo["s5_pairs"]) == set(PRESET_IDS)
    assert summary["matrix_review"]["state"] == "PENDING_HUMAN_REVIEW"
    assert "national" not in json.dumps(summary).casefold()


def test_cli_template_and_preflight_never_send_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template_path = tmp_path / "applicability.json"
    assert main(["tjgo", "--template-out", str(template_path)]) == 0
    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert len(template["tribunals"]["tjgo"]) == 4

    monkeypatch.setenv("AGROJUD_ENV", "real")
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("DATAJUD_API_KEY", API_KEY)
    output_dir = tmp_path / "reports"
    exit_code = main(
        [
            "tjgo",
            "--from-date",
            FILED_FROM.isoformat(),
            "--to-date",
            FILED_TO.isoformat(),
            "--output-dir",
            str(output_dir),
        ]
    )
    batch_path = next(output_dir.glob("batch-*.json"))
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    endpoint_path = next(output_dir.glob("tribunal-tjgo-*.json"))
    endpoint = json.loads(endpoint_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert batch["requests_made"] == endpoint["requests_made"] == 0
    assert endpoint["environment"] == "real"
    assert endpoint["terms_review"]["operator_confirmed_before_probe"] is False
    assert set(endpoint["terms_review"]["references"]) >= {
        "https://atos.cnj.jus.br/atos/detalhar/3453",
        "https://atos.cnj.jus.br/atos/detalhar/6972",
    }
    assert "termos" in endpoint["s1_s2"]["capabilities"]["http_access"]["evidence"]["reason"]


def test_cli_demo_or_missing_key_writes_a_zero_request_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("AGROJUD_ENV", "demo")
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    output_dir = tmp_path / "demo-reports"
    result = main(
        [
            "tjgo",
            "--from-date",
            FILED_FROM.isoformat(),
            "--to-date",
            FILED_TO.isoformat(),
            "--output-dir",
            str(output_dir),
        ]
    )
    report_path = next(output_dir.glob("tribunal-tjgo-*.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert result == 2
    assert report["requests_made"] == 0
    assert report["environment"] == "demo"
    assert report["runtime_activation_changed"] is False


def test_cli_real_environment_without_key_writes_zero_request_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("AGROJUD_ENV", "real")
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    output_dir = tmp_path / "missing-key-reports"
    result = main(
        [
            "tjgo",
            "--from-date",
            FILED_FROM.isoformat(),
            "--to-date",
            FILED_TO.isoformat(),
            "--output-dir",
            str(output_dir),
            "--confirm-terms-reviewed",
        ]
    )
    report = json.loads(next(output_dir.glob("tribunal-tjgo-*.json")).read_text(encoding="utf-8"))
    assert result == 2
    assert report["requests_made"] == 0
    assert (
        "DATAJUD_API_KEY ausente"
        in report["s1_s2"]["capabilities"]["http_access"]["evidence"]["reason"]
    )


def _write_local_batch(directory: Path, aliases: list[str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    result = DataJudMultiTribunalProbe(
        aliases,
        filed_from=FILED_FROM,
        filed_to=FILED_TO,
        preflight_reason="Teste local sem chamadas HTTP.",
        environment="real",
    ).run()
    _write_batch_reports(result, directory)
    summary = cast(dict[str, object], result["summary"])
    return directory / f"batch-{summary['run_id']}.json"


def _mutate_endpoint_report(
    batch_path: Path,
    alias: str,
    *,
    capability_states: dict[str, str] | None = None,
    pair_states: dict[str, str] | None = None,
    stale_preset: str | None = None,
) -> None:
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    reference = next(item for item in batch["tribunal_reports"] if item["alias"] == alias)
    report_path = batch_path.parent / reference["file_key"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    recorded_requests = 0
    for capability, state in (capability_states or {}).items():
        report["s1_s2"]["capabilities"][capability]["state"] = state
    if any(state in {"VALIDATED", "INCOMPATIBLE"} for state in (capability_states or {}).values()):
        report["environment"] = "real"
        report["terms_review"]["operator_confirmed_before_probe"] = True
        report["s1_s2"]["requests"] = [{"name": "synthetic_validation", "outcome": "SUCCESS"}]
        report["s1_s2"]["requests_made"] = 1
        recorded_requests += 1
    states = {item["state"] for item in report["s1_s2"]["capabilities"].values()}
    if "INCOMPATIBLE" in states:
        report["s1_s2"]["aggregate_state"] = "INCOMPATIBLE"
    elif states == {"VALIDATED"}:
        report["s1_s2"]["aggregate_state"] = "VALIDATED"
    elif states == {"NOT_RUN"}:
        report["s1_s2"]["aggregate_state"] = "NOT_RUN"
    else:
        report["s1_s2"]["aggregate_state"] = "INCONCLUSIVE"
    for pair in report["s5_pairs"]:
        state = (pair_states or {}).get(pair["preset_id"])
        if state is not None:
            pair["applicability"] = {
                "state": "APPLICABLE",
                "tpu_version": load_catalog().tpu_version,
                "reference_url": TPU_REFERENCE_URL,
                "rationale": "Aplicabilidade sintética de teste.",
            }
            if state == "VALIDATED":
                query_state = sample_state = "VALIDATED"
            elif state == "INCOMPATIBLE":
                query_state, sample_state = "INCOMPATIBLE", "VALIDATED"
            else:
                query_state = sample_state = "INCONCLUSIVE"
            pair["query_status"] = {"state": query_state, "evidence": {}}
            pair["sample_status"] = {"state": sample_state, "evidence": {}}
            pair["pair_state"] = state
            if state in {"VALIDATED", "INCOMPATIBLE"}:
                report["environment"] = "real"
                report["terms_review"]["operator_confirmed_before_probe"] = True
                pair["requests"] = [{"name": "synthetic_validation", "outcome": "SUCCESS"}]
                pair["requests_made"] = 1
                recorded_requests += 1
        if pair["preset_id"] == stale_preset:
            pair["preset_version"] = "retired-preset-version"
    report["requests_made"] = recorded_requests
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    batch["requests_made"] += recorded_requests
    batch_path.write_text(
        json.dumps(batch, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_matrix_merge_seven_batches_lists_33_by_four_without_claiming_coverage(
    tmp_path: Path,
) -> None:
    aliases = list(TRIBUNAL_ALLOWLIST)
    batch_paths = [
        _write_local_batch(tmp_path / f"batch-{index}", aliases[index : index + 5])
        for index in range(0, len(aliases), MAX_TRIBUNALS_PER_BATCH)
    ]

    matrix = merge_batch_reports(batch_paths)

    rows = cast(list[dict[str, object]], matrix["coverage_matrix"])
    assert len(batch_paths) == 7
    assert len(rows) == len(TRIBUNAL_ALLOWLIST) == 33
    assert sum(len(cast(dict[str, object], row["s5_pairs"])) for row in rows) == 132
    assert all(
        set(cast(dict[str, object], row["s1_s2_capabilities"]))
        == {
            "http_access",
            "envelope",
            "essential_fields",
            "tribunal_identity",
            "date_filter",
            "query_by_case_number",
            "source_identity",
            "sort_timestamp",
            "compound_sort",
            "pagination",
        }
        for row in rows
    )
    tjgo = next(row for row in rows if row["alias"] == "tjgo")
    assert (
        cast(dict[str, object], tjgo["s1_s2_capabilities"])["http_access"]["state"]
        == "INCONCLUSIVE"
    )
    assert matrix["matrix_review"] == {"state": "PENDING_HUMAN_REVIEW"}
    assert matrix["coverage_status"] == "PENDING_HUMAN_REVIEW"
    assert "NATIONAL_COVERAGE_VALIDATED" not in json.dumps(matrix)


def test_matrix_merge_deduplicates_identical_run_and_rejects_divergent_run(
    tmp_path: Path,
) -> None:
    original_dir = tmp_path / "original"
    batch_path = _write_local_batch(original_dir, ["tjgo"])
    merged = merge_batch_reports([batch_path, batch_path])
    assert merged["batch_count"] == 1
    assert len(merged["attempts"]) == 1

    duplicate_dir = tmp_path / "duplicate"
    duplicate_dir.mkdir()
    divergent = json.loads(batch_path.read_text(encoding="utf-8"))
    divergent["duration_ms"] = divergent.get("duration_ms", 0) + 1
    duplicate_path = duplicate_dir / batch_path.name
    duplicate_path.write_text(json.dumps(divergent), encoding="utf-8")
    endpoint_report = next(original_dir.glob("tribunal-tjgo-*.json"))
    (duplicate_dir / endpoint_report.name).write_bytes(endpoint_report.read_bytes())
    with pytest.raises(ValueError, match="resumos batch divergentes"):
        merge_batch_reports([batch_path, duplicate_path])


def test_matrix_merge_rejects_allowlist_mismatch(tmp_path: Path) -> None:
    batch_path = _write_local_batch(tmp_path / "batch", ["tjgo"])
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    batch["allowlist_version"] = "older-allowlist"
    batch_path.write_text(json.dumps(batch), encoding="utf-8")
    with pytest.raises(ValueError, match="allowlist divergente"):
        merge_batch_reports([batch_path])


def test_matrix_merge_does_not_accept_validation_without_real_gates(tmp_path: Path) -> None:
    batch_path = _write_local_batch(tmp_path / "batch", ["tjgo"])
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    reference = batch["tribunal_reports"][0]
    report_path = batch_path.parent / reference["file_key"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["s1_s2"]["capabilities"]["http_access"]["state"] = "VALIDATED"
    report["s1_s2"]["aggregate_state"] = "INCONCLUSIVE"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="não prova chamada real autorizada"):
        merge_batch_reports([batch_path])


def test_matrix_cli_merges_and_writes_an_audited_review_copy(tmp_path: Path) -> None:
    batch_path = _write_local_batch(tmp_path / "batch", ["tjgo"])
    matrix_path = tmp_path / "merged.json"
    assert matrix_main(["merge", "--batch", str(batch_path), "--output", str(matrix_path)]) == 0

    reviewed_path = tmp_path / "reviewed.json"
    assert (
        matrix_main(
            [
                "review",
                "--matrix",
                str(matrix_path),
                "--output",
                str(reviewed_path),
                "--reviewer",
                "Revisora CLI",
                "--decision",
                "APPROVED",
                "--rationale",
                "Lote local parcial revisado.",
            ]
        )
        == 0
    )
    merged = json.loads(matrix_path.read_text(encoding="utf-8"))
    reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
    assert merged["matrix_review"]["state"] == "PENDING_HUMAN_REVIEW"
    assert reviewed["matrix_review"]["decision"] == "APPROVED"
    assert reviewed["matrix_review"]["reviewer"] == "Revisora CLI"
    assert (
        reviewed["matrix_review"]["matrix_sha256"]
        == hashlib.sha256(matrix_path.read_bytes()).hexdigest()
    )
    assert reviewed["coverage_status"] == "APPROVED_PARTIAL"


def test_matrix_cli_reviews_without_reviewer_and_preserves_audit_evidence(
    tmp_path: Path,
) -> None:
    batch_path = _write_local_batch(tmp_path / "batch", ["tjgo"])
    matrix_path = tmp_path / "merged.json"
    assert matrix_main(["merge", "--batch", str(batch_path), "--output", str(matrix_path)]) == 0

    source_bytes = matrix_path.read_bytes()
    source = json.loads(source_bytes)
    reviewed_path = tmp_path / "reviewed.json"
    assert (
        matrix_main(
            [
                "review",
                "--matrix",
                str(matrix_path),
                "--output",
                str(reviewed_path),
                "--decision",
                "APPROVED",
                "--rationale",
                "Aprovação limitada às evidências VALIDATED.",
            ]
        )
        == 0
    )

    reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
    review = reviewed["matrix_review"]
    assert "reviewer" not in review
    assert review["decision"] == "APPROVED"
    assert review["rationale"] == "Aprovação limitada às evidências VALIDATED."
    assert review["reviewed_at"].endswith("+00:00")
    assert review["matrix_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert reviewed["coverage_status"] == "APPROVED_PARTIAL"
    assert reviewed["coverage_assessment"] == "EVIDENCE_INCOMPLETE"
    assert reviewed["coverage_matrix"] == source["coverage_matrix"]
    assert reviewed["attempts"] == source["attempts"]
    assert matrix_path.read_bytes() == source_bytes


def test_matrix_merge_preserves_attempts_and_review_resolves_only_with_validated_run(
    tmp_path: Path,
) -> None:
    first = _write_local_batch(tmp_path / "first", ["tjgo"])
    second = _write_local_batch(tmp_path / "second", ["tjgo"])
    _mutate_endpoint_report(
        first,
        "tjgo",
        capability_states={"http_access": "VALIDATED", "tribunal_identity": "INCOMPATIBLE"},
        pair_states={PRESET_IDS[0]: "INCOMPATIBLE"},
    )
    _mutate_endpoint_report(
        second,
        "tjgo",
        capability_states={"http_access": "INCONCLUSIVE", "tribunal_identity": "VALIDATED"},
        pair_states={PRESET_IDS[0]: "VALIDATED"},
    )
    matrix = merge_batch_reports([first, second])
    rows = cast(list[dict[str, object]], matrix["coverage_matrix"])
    tjgo = next(row for row in rows if row["alias"] == "tjgo")
    capabilities = cast(dict[str, dict[str, object]], tjgo["s1_s2_capabilities"])
    pairs = cast(dict[str, dict[str, object]], tjgo["s5_pairs"])
    first_run_id = json.loads(first.read_text(encoding="utf-8"))["run_id"]
    second_run_id = json.loads(second.read_text(encoding="utf-8"))["run_id"]
    assert capabilities["http_access"]["state"] == "VALIDATED"
    assert capabilities["tribunal_identity"]["state"] == "INCOMPATIBLE"
    assert pairs[PRESET_IDS[0]]["pair_state"] == "INCOMPATIBLE"
    assert len(capabilities["http_access"]["attempts"]) == 2
    assert pairs[PRESET_IDS[0]]["attempt_count"] == 2

    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    bad_resolutions_path = tmp_path / "bad-resolutions.json"
    bad_resolutions_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resolutions": [
                    {
                        "alias": "tjgo",
                        "target": "s1_s2",
                        "capability": "tribunal_identity",
                        "selected_run_id": first_run_id,
                        "rationale": "Essa tentativa não foi validada.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="não tem evidência VALIDATED atual"):
        review_coverage_matrix(
            matrix_path,
            tmp_path / "bad-review.json",
            reviewer="Revisora local",
            decision="APPROVED",
            rationale="Revisão de teste.",
            resolutions_path=bad_resolutions_path,
        )

    resolutions_path = tmp_path / "resolutions.json"
    resolutions_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resolutions": [
                    {
                        "alias": "tjgo",
                        "target": "s1_s2",
                        "capability": "tribunal_identity",
                        "selected_run_id": second_run_id,
                        "rationale": "A evidência validada posterior foi revisada.",
                    },
                    {
                        "alias": "tjgo",
                        "target": "s5",
                        "preset_id": PRESET_IDS[0],
                        "selected_run_id": second_run_id,
                        "rationale": "A evidência S5 validada foi revisada.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    source_bytes = matrix_path.read_bytes()
    reviewed_path = tmp_path / "reviewed.json"
    reviewed = review_coverage_matrix(
        matrix_path,
        reviewed_path,
        reviewer="Revisora local",
        decision="APPROVED",
        rationale="Matriz parcial revisada.",
        resolutions_path=resolutions_path,
    )
    reviewed_rows = cast(list[dict[str, object]], reviewed["coverage_matrix"])
    reviewed_tjgo = next(row for row in reviewed_rows if row["alias"] == "tjgo")
    assert (
        cast(dict[str, dict[str, object]], reviewed_tjgo["s1_s2_capabilities"])[
            "tribunal_identity"
        ]["state"]
        == "VALIDATED"
    )
    assert (
        cast(dict[str, dict[str, object]], reviewed_tjgo["s5_pairs"])[PRESET_IDS[0]]["pair_state"]
        == "VALIDATED"
    )
    assert (
        len(
            cast(dict[str, dict[str, object]], reviewed_tjgo["s1_s2_capabilities"])[
                "tribunal_identity"
            ]["attempts"]
        )
        == 2
    )
    assert reviewed["coverage_status"] == "APPROVED_PARTIAL"
    assert reviewed["matrix_review"]["matrix_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert matrix_path.read_bytes() == source_bytes


def test_matrix_merge_marks_previous_preset_version_stale(tmp_path: Path) -> None:
    batch_path = _write_local_batch(tmp_path / "batch", ["tjgo"])
    _mutate_endpoint_report(
        batch_path,
        "tjgo",
        pair_states={PRESET_IDS[0]: "VALIDATED"},
        stale_preset=PRESET_IDS[0],
    )
    matrix = merge_batch_reports([batch_path])
    tjgo = next(
        row
        for row in cast(list[dict[str, object]], matrix["coverage_matrix"])
        if row["alias"] == "tjgo"
    )
    pair = cast(dict[str, dict[str, object]], tjgo["s5_pairs"])[PRESET_IDS[0]]
    assert pair["pair_state"] == "NOT_RUN"
    assert pair["current_attempt_count"] == 0
    assert pair["attempts"][0]["current_for_coverage"] is False
    assert "PRESET_VERSION_CHANGED" in pair["attempts"][0]["stale_reasons"]


def test_probe_cli_returns_one_for_s5_incompatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = str(uuid.uuid4())

    class IncompatibleS5Probe:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run(self, *, started_at: object) -> dict[str, object]:
            del started_at
            report = {
                "alias": "tjgo",
                "run_id": None,
                "s1_s2": {
                    "requests": [],
                    "capabilities": {
                        name: {"state": "VALIDATED"}
                        for name in (
                            "http_access",
                            "envelope",
                            "essential_fields",
                            "tribunal_identity",
                            "date_filter",
                            "query_by_case_number",
                            "source_identity",
                            "sort_timestamp",
                            "compound_sort",
                            "pagination",
                        )
                    },
                },
                "s5_pairs": [
                    {
                        "pair_state": "INCOMPATIBLE",
                        "query_status": {"state": "INCOMPATIBLE"},
                        "sample_status": {"state": "VALIDATED"},
                        "requests": [],
                    }
                ],
            }
            return {
                "summary": {"run_id": run_id, "requests_made": 1},
                "tribunal_reports": [report],
            }

    monkeypatch.setenv("AGROJUD_ENV", "real")
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    monkeypatch.setenv("DATAJUD_API_KEY", API_KEY)
    monkeypatch.setattr(multitribunal_module, "DataJudMultiTribunalProbe", IncompatibleS5Probe)
    assert (
        main(
            [
                "tjgo",
                "--from-date",
                FILED_FROM.isoformat(),
                "--to-date",
                FILED_TO.isoformat(),
                "--output-dir",
                str(tmp_path / "s5-incompatible"),
                "--confirm-terms-reviewed",
            ]
        )
        == 1
    )
