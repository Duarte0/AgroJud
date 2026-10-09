"""Source query, response-envelope, and normalization contracts."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from agrojud.sources import (
    SourceError,
    SourceErrorCode,
    SourceQuery,
    build_query_by_case_number,
    normalize_source_cover,
)
from agrojud.sources.contracts import parse_source_page


def raw_hit(
    *,
    source_id: object = "TJGO_100_G1_123_00000010020248090001",
    process_number: object = "00000010020248090001",
    tribunal: object = "TJGO",
    sort: object = [1735689600000],
    source: object | None = None,
) -> dict[str, object]:
    if source is None:
        source = {
            "id": source_id,
            "numeroProcesso": process_number,
            "tribunal": tribunal,
            "classe": {"codigo": 100, "nome": "Classe sintética"},
            "campoNovo": {"preservar": True},
            "movimentos": [{"codigo": 26, "nome": "Distribuição"}],
        }
    return {"_id": source_id, "_source": source, "sort": sort, "_score": None}


def valid_response(*hits: dict[str, object]) -> dict[str, object]:
    return {
        "took": 4,
        "hits": {
            "total": {"value": len(hits), "relation": "eq"},
            "hits": list(hits),
        },
    }


def test_query_is_immutable_allowlisted_and_half_open() -> None:
    query = SourceQuery(
        filed_from=date(2026, 1, 1),
        filed_to=date(2026, 2, 1),
        class_codes=(1116, 100),
        subject_codes=(4968,),
        court_unit_code=13597,
        preset_id="rural",
        preset_version="2",
    )

    assert query.tribunal == "TJGO"
    assert query.class_codes == (100, 1116)
    with pytest.raises(FrozenInstanceError):
        query.tribunal = "TJSP"  # type: ignore[misc]


def test_query_by_cnj_accepts_formatted_number_and_removes_punctuation() -> None:
    query = build_query_by_case_number("0000001-00.2024.8.09.0001")

    assert query.process_number == "00000010020248090001"
    assert query.tribunal == "TJGO"


@pytest.mark.parametrize(
    "query_fields",
    [
        {"tribunal": "TJSP", "class_codes": (1116,)},
        {"filed_from": date(2026, 1, 1)},
        {"filed_from": date(2026, 2, 1), "filed_to": date(2026, 1, 1)},
        {"class_codes": (0,)},
        {"process_number": "123"},
        {"preset_id": "rural"},
        {},
    ],
)
def test_invalid_query_fails_with_typed_validation_error(
    query_fields: dict[str, object],
) -> None:
    with pytest.raises(SourceError) as captured:
        SourceQuery(**query_fields)  # type: ignore[arg-type]

    assert captured.value.code is SourceErrorCode.VALIDATION


def test_source_page_preserves_raw_hit_and_total_relation() -> None:
    response = valid_response(raw_hit(sort=["timestamp opaque", 2]))
    response["hits"]["total"] = {"value": 10_000, "relation": "gte"}  # type: ignore[index]

    page = parse_source_page(response, datetime(2026, 10, 8, tzinfo=UTC))
    cover = normalize_source_cover(page.hits[0])

    assert page.total_value == 10_000
    assert page.total_relation == "gte"
    assert page.cursor_final == ("timestamp opaque", 2)
    assert page.hits[0].raw["_score"] is None
    assert cover.source_id == "TJGO_100_G1_123_00000010020248090001"
    assert cover.process_number == "00000010020248090001"
    assert cover.raw_source["campoNovo"] == {"preservar": True}
    assert cover.raw_source["movimentos"] == [{"codigo": 26, "nome": "Distribuição"}]


def test_invalid_payload_stays_in_successful_page_for_later_quarantine() -> None:
    page = parse_source_page(
        valid_response(raw_hit(process_number="not-a-cnj")),
        datetime(2026, 10, 8, tzinfo=UTC),
    )

    assert len(page.hits) == 1
    assert page.hits[0].raw["_source"]["numeroProcesso"] == "not-a-cnj"  # type: ignore[index]
    with pytest.raises(SourceError) as captured:
        normalize_source_cover(page.hits[0])
    assert captured.value.code is SourceErrorCode.VALIDATION


@pytest.mark.parametrize(
    "hit",
    [
        raw_hit(
            source_id="source-id-do-hit",
            source={
                "id": "different-id",
                "numeroProcesso": "00000010020248090001",
                "tribunal": "TJGO",
            },
        ),
        raw_hit(source={"id": "id", "numeroProcesso": "00000010020248090001", "tribunal": "TJGO"}),
        raw_hit(tribunal="TJSP"),
        raw_hit(
            source={"id": "id", "numeroProcesso": "00000010020248090001", "tribunal": "TJGO"},
            source_id="other-id",
        ),
    ],
)
def test_normalization_rejects_unapproved_or_invalid_source_identity(
    hit: dict[str, object],
) -> None:
    page = parse_source_page(valid_response(hit), datetime(2026, 10, 8, tzinfo=UTC))

    with pytest.raises(SourceError) as captured:
        normalize_source_cover(page.hits[0])
    assert captured.value.code is SourceErrorCode.VALIDATION


@pytest.mark.parametrize(
    "response",
    [
        [],
        {"hits": []},
        {"hits": {"hits": {}}},
        {"hits": {"hits": [None]}},
        {"hits": {"hits": [{"_id": "x", "_source": {}}]}},
        {"hits": {"total": {"value": "1"}, "hits": []}},
    ],
)
def test_malformed_response_envelope_is_contract_error(response: object) -> None:
    with pytest.raises(SourceError) as captured:
        parse_source_page(response, datetime(2026, 10, 8, tzinfo=UTC))

    assert captured.value.code is SourceErrorCode.CONTRACT


def test_empty_hits_is_successful_empty_page() -> None:
    page = parse_source_page(
        {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}},
        datetime(2026, 10, 8, tzinfo=UTC),
    )

    assert page.is_empty
    assert page.hits == ()
    assert page.cursor_final is None
    assert page.total_value == 0


def test_missing_optional_total_metadata_is_preserved_as_absent() -> None:
    page = parse_source_page(
        {"hits": {"hits": []}},
        datetime(2026, 10, 8, tzinfo=UTC),
    )

    assert page.is_empty
    assert page.total_value is None
    assert page.total_relation is None
