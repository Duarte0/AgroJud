"""Acceptance tests for the versioned thematic catalog and its collection snapshot."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from importlib.resources import files
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import agrojud.sources.catalog as catalog_module
from agrojud.services.collection import (
    build_collection_request,
    source_query_from_snapshot,
)
from agrojud.sources.catalog import (
    compile_preset,
    get_item_availability,
    list_candidate_signals,
    list_presets,
    load_catalog,
    validate_catalog_document,
)
from agrojud.sources.contracts import (
    SourceError,
    SourceErrorCode,
    SourceQuery,
    build_datajud_payload,
)


def raw_catalog() -> dict[str, Any]:
    raw = files("agrojud.sources").joinpath("thematic_catalog.v1.json").read_text(encoding="utf-8")
    return json.loads(raw)


def test_catalog_records_all_families_and_validates_every_included_tpu_code() -> None:
    catalog = load_catalog()

    assert catalog.catalog_version == "1.0.0"
    assert catalog.tpu_version == "06/10/2026"
    assert {family.id for family in catalog.families} == {
        "rural_explicit",
        "executions_broad",
        "recovery_judicial_broad",
        "signal_penhora",
        "signal_leilao",
        "signal_recovery_judicial",
    }
    assert all(
        family.investigation_state == "investigated" or family.pending_reason
        for family in catalog.families
    )
    for item in catalog.items:
        assert set(item.evidence) == {"tpu_status", "query_status", "sample_status"}
        for dimension, codes in item.filters.items():
            for code in codes:
                evidence = catalog.codes[(dimension, code)]
                assert evidence.situation == "A"
                assert evidence.tjgo_first_grade or evidence.tjgo_second_grade
    assert len(list_presets()) == 4
    assert len(list_candidate_signals()) == 3


def test_unknown_catalog_code_is_rejected_without_official_evidence() -> None:
    document = raw_catalog()
    document["items"][0]["filters"]["subjects"].append(999_999)

    with pytest.raises(ValueError, match="sem evidência TPU"):
        validate_catalog_document(document)


def test_descendant_expansion_requires_separate_code_level_evidence() -> None:
    document = raw_catalog()
    document["items"][0]["include_descendants"] = True

    with pytest.raises(ValueError, match="expandir descendentes"):
        validate_catalog_document(document)


def test_real_presets_stay_disabled_and_demo_is_labeled_synthetic() -> None:
    for item in list_presets():
        real = get_item_availability(item.id, "real")
        demo = get_item_availability(item.id, "demo")
        assert not real.enabled
        assert any("query_status: inconclusive" in reason for reason in real.reasons)
        assert any("sample_status: inconclusive" in reason for reason in real.reasons)
        assert demo.enabled
        assert demo.label == "demonstrativo sintético"
        assert any("não valida o TJGO" in reason for reason in demo.reasons)

    for signal in list_candidate_signals():
        assert not get_item_availability(signal.id, "real").enabled
        assert not get_item_availability(signal.id, "demo").enabled


def test_real_compile_reports_why_the_preset_is_unavailable() -> None:
    with pytest.raises(SourceError) as captured:
        compile_preset("rural.credito_contratos", environment="real")

    assert captured.value.code is SourceErrorCode.VALIDATION
    assert captured.value.validation_code == "PRESET_UNAVAILABLE"
    assert "query_status: inconclusive" in captured.value.message
    assert "sample_status: inconclusive" in captured.value.message


def test_unknown_or_candidate_items_cannot_compile_as_presets() -> None:
    with pytest.raises(SourceError, match="não existe no catálogo"):
        compile_preset("preset.inventado", environment="demo")
    with pytest.raises(SourceError, match="não é um preset"):
        compile_preset("sinal.penhora", environment="demo")


def test_user_end_date_is_inclusive_and_saved_as_exclusive() -> None:
    compiled = compile_preset(
        "rural.credito_contratos",
        environment="demo",
        filed_from=date(2026, 3, 15),
        filed_through_inclusive=date(2026, 3, 18),
    )

    assert compiled.query.filed_from == date(2026, 3, 15)
    assert compiled.query.filed_to == date(2026, 3, 19)
    assert compiled.window_template == {
        "kind": "explicit_inclusive_dates",
        "filed_from": "2026-03-15",
        "filed_through_inclusive": "2026-03-18",
        "timezone": "America/Sao_Paulo",
    }
    assert compiled.snapshot()["resolved_interval"] == {
        "filed_from": "2026-03-15",
        "filed_to_exclusive": "2026-03-19",
    }


def test_default_window_uses_twelve_calendar_months_and_sao_paulo_date() -> None:
    compiled = compile_preset(
        "execucoes.amplas",
        environment="demo",
        reference_time=datetime(2026, 10, 9, 2, 0, tzinfo=UTC),
    )

    assert compiled.window_template["kind"] == "relative_calendar_months"
    assert compiled.window_template["months"] == 12
    assert compiled.query.filed_from == date(2025, 10, 8)
    assert compiled.query.filed_to == date(2026, 10, 8)


def test_default_window_clamps_leap_day_when_subtracting_calendar_months() -> None:
    compiled = compile_preset(
        "execucoes.amplas",
        environment="demo",
        reference_time=datetime(2024, 2, 29, 12, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )

    assert compiled.query.filed_from == date(2023, 2, 28)
    assert compiled.query.filed_to == date(2024, 2, 29)


@pytest.mark.parametrize(
    "arguments",
    [
        {"filed_from": date(2026, 1, 1)},
        {"filed_through_inclusive": date(2026, 1, 2)},
        {"filed_from": date(2026, 1, 3), "filed_through_inclusive": date(2026, 1, 2)},
        {"reference_time": datetime(2026, 1, 1, 12)},
    ],
)
def test_invalid_date_input_is_rejected(arguments: dict[str, object]) -> None:
    with pytest.raises(SourceError):
        compile_preset("rural.credito_contratos", environment="demo", **arguments)  # type: ignore[arg-type]


def test_allowlisted_compiler_uses_or_within_dimensions_and_and_between_dimensions() -> None:
    query = SourceQuery(
        filed_from=date(2026, 1, 1),
        filed_to=date(2026, 2, 1),
        class_codes=(12154, 159),
        subject_codes=(4964, 10501),
        movement_codes=(11382, 311),
    )

    payload = build_datajud_payload(query, None, 100)
    filters = payload["query"]["bool"]["filter"]  # type: ignore[index]
    by_key = {
        next(iter(value)): next(iter(value.values()))
        for clause in filters
        for value in [clause.get("terms", {})]
        if value
    }

    assert by_key["classe.codigo"] == [159, 12154]
    assert by_key["assuntos.codigo"] == [4964, 10501]
    assert by_key["movimentos.codigo"] == [311, 11382]
    assert len(filters) == 5  # tribunal, date, and three separate AND filters


def test_saved_collection_freezes_catalog_version_justification_and_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiled = compile_preset(
        "execucoes.amplas",
        environment="demo",
        reference_time=datetime(2026, 10, 9, 12, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )
    request = build_collection_request(
        mode="demo",
        source="synthetic",
        job_type="discovery",
        query=compiled.query,
        catalog_snapshot=compiled.snapshot(),
    )
    saved = request.resolved_query["catalog_snapshot"]
    assert saved["catalog_version"] == "1.0.0"  # type: ignore[index]
    assert saved["evidence_report"] == "docs/evidence/catalogo-tematico-tpu-2026-10-09.json"  # type: ignore[index]
    assert saved["preset_version"] == "1.0.0"  # type: ignore[index]
    assert saved["justification"] == compiled.item.justification  # type: ignore[index]
    assert saved["effective_codes"]["class_codes"] == list(compiled.query.class_codes)  # type: ignore[index]
    assert saved["window_template"]["kind"] == "relative_calendar_months"  # type: ignore[index]
    assert saved["resolved_interval"]["filed_from"] == "2025-10-09"  # type: ignore[index]
    assert saved["rural_link_status"] == "not_confirmed"  # type: ignore[index]

    def changed_catalog() -> Any:
        raise AssertionError("A saved query must not recompile against today's catalog.")

    monkeypatch.setattr(catalog_module, "load_catalog", changed_catalog)
    rebuilt = source_query_from_snapshot(request.resolved_query, request.sort)
    assert rebuilt == compiled.query
    assert request.resolved_query["catalog_snapshot"] == saved


def test_preset_collection_requires_a_consistent_snapshot() -> None:
    compiled = compile_preset("rural.credito_contratos", environment="demo")

    with pytest.raises(SourceError, match="precisam salvar"):
        build_collection_request(
            mode="demo",
            source="synthetic",
            job_type="discovery",
            query=compiled.query,
        )

    snapshot = compiled.snapshot()
    with pytest.raises(SourceError, match="ambiente da coleta"):
        build_collection_request(
            mode="real",
            source="datajud",
            job_type="discovery",
            query=compiled.query,
            catalog_snapshot=snapshot,
        )

    snapshot["effective_codes"]["subject_codes"] = [999_999]  # type: ignore[index]
    with pytest.raises(SourceError, match="filtros efetivos"):
        build_collection_request(
            mode="demo",
            source="synthetic",
            job_type="discovery",
            query=compiled.query,
            catalog_snapshot=snapshot,
        )


def test_recovery_presets_return_filter_code_explanations_and_do_not_confirm_rural_link() -> None:
    compiled = compile_preset(
        "recuperacao_judicial.por_assunto",
        environment="demo",
        reference_time=datetime(2026, 10, 9, 12, tzinfo=ZoneInfo("America/Sao_Paulo")),
    )

    assert compiled.query.class_codes == ()
    assert compiled.query.subject_codes == (4993, 13277)
    assert {entry["code"] for entry in compiled.explanations} == {4993, 13277}
    assert {entry["name"] for entry in compiled.explanations} == {
        "Recuperação judicial e Falência",
        "Recuperação Judicial",
    }
    assert compiled.snapshot()["rural_link_status"] == "not_confirmed"
