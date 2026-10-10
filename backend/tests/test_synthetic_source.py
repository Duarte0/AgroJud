"""Deterministic fixture pagination, empty results, mutations, and injected errors."""

from datetime import UTC, datetime

import pytest

from agrojud.sources import (
    TIMESTAMP_SORT,
    SourceError,
    SourceErrorCode,
    SourceQuery,
    SyntheticPageFixture,
    SyntheticQueryFixture,
    SyntheticSourceAdapter,
    build_query_by_case_number,
)

FIXED_TIME = datetime(2026, 10, 8, tzinfo=UTC)
QUERY = SourceQuery(class_codes=(1116,), sort=TIMESTAMP_SORT)


def hit(index: int, cursor_value: object) -> dict[str, object]:
    source_id = f"fixture-{index}"
    return {
        "_id": source_id,
        "_source": {
            "id": source_id,
            "numeroProcesso": f"000000{index}0020248090001",
            "tribunal": "TJGO",
            "optional": {"value": index},
        },
        "sort": [cursor_value],
    }


def page_fixture(*hits: dict[str, object]) -> SyntheticPageFixture:
    return SyntheticPageFixture(
        hits=tuple(hits),
        total_value=len(hits),
        total_relation="eq",
        responded_at=FIXED_TIME,
    )


def test_fixture_pages_preserve_opaque_cursor_and_repeat_deterministically() -> None:
    fixture = SyntheticQueryFixture(
        pages=(
            page_fixture(hit(1, "opaque::cursor::one"), hit(2, "opaque::cursor::two")),
            page_fixture(hit(3, "opaque::cursor::three")),
        )
    )
    adapter = SyntheticSourceAdapter({QUERY: fixture})

    first = adapter.fetch_page(QUERY, None, 1)
    repeated = adapter.fetch_page(QUERY, None, 1)
    second = adapter.fetch_page(QUERY, first.cursor_final, 1)
    third = adapter.fetch_page(QUERY, second.cursor_final, 1)

    assert first.hits[0].source_id == repeated.hits[0].source_id
    assert first.cursor_final == ("opaque::cursor::one",)
    assert second.cursor_final == ("opaque::cursor::two",)
    assert third.cursor_final == ("opaque::cursor::three",)
    assert third.responded_at == FIXED_TIME
    assert adapter.fetch_page(QUERY, third.cursor_final, 1).is_empty


def test_empty_query_is_a_successful_fixture_not_a_missing_fixture() -> None:
    adapter = SyntheticSourceAdapter({QUERY: SyntheticQueryFixture()})

    page = adapter.fetch_page(QUERY, None, 100)

    assert page.is_empty
    assert page.total_value == 0
    assert page.total_relation == "eq"


def test_fixture_can_mutate_between_executions() -> None:
    adapter = SyntheticSourceAdapter({QUERY: SyntheticQueryFixture((page_fixture(hit(1, 1)),))})
    initial = adapter.fetch_page(QUERY, None, 100)

    adapter.replace_fixture(QUERY, SyntheticQueryFixture((page_fixture(hit(2, 2)),)))
    updated = adapter.fetch_page(QUERY, None, 100)

    assert initial.hits[0].source_id == "fixture-1"
    assert updated.hits[0].source_id == "fixture-2"


def test_injected_error_is_typed_and_does_not_fall_back_to_empty() -> None:
    injected = SourceError(
        SourceErrorCode.RATE_LIMIT,
        "fixture rate limit",
        status_code=429,
        retry_after="12",
    )
    fixture = SyntheticQueryFixture(
        pages=(page_fixture(hit(1, "first")), page_fixture(hit(2, "second"))),
        errors=((("first",), injected),),
    )
    adapter = SyntheticSourceAdapter({QUERY: fixture})
    first = adapter.fetch_page(QUERY, None, 100)

    with pytest.raises(SourceError) as captured:
        adapter.fetch_page(QUERY, first.cursor_final, 100)

    assert captured.value.code is SourceErrorCode.RATE_LIMIT
    assert captured.value.status_code == 429
    assert captured.value.retry_after == "12"


def test_missing_or_repeated_cursor_fails_explicitly() -> None:
    adapter = SyntheticSourceAdapter({QUERY: SyntheticQueryFixture((page_fixture(hit(1, 1)),))})

    with pytest.raises(SourceError) as captured:
        adapter.fetch_page(QUERY, (999,), 100)
    assert captured.value.code is SourceErrorCode.VALIDATION

    repeated_cursor_fixture = SyntheticQueryFixture((page_fixture(hit(1, 1), hit(2, 1)),))
    adapter.replace_fixture(QUERY, repeated_cursor_fixture)
    with pytest.raises(SourceError) as duplicate:
        adapter.fetch_page(QUERY, (1,), 100)
    assert duplicate.value.code is SourceErrorCode.CONTRACT


def test_missing_fixture_does_not_turn_into_an_empty_success() -> None:
    adapter = SyntheticSourceAdapter()

    with pytest.raises(SourceError) as captured:
        adapter.fetch_page(QUERY, None, 100)

    assert captured.value.code is SourceErrorCode.VALIDATION


def test_fetch_by_case_number_uses_a_locally_built_query() -> None:
    query = build_query_by_case_number("0000001-00.2024.8.09.0001")
    adapter = SyntheticSourceAdapter(
        {query: SyntheticQueryFixture((page_fixture(hit(1, "exact-number")),))}
    )

    result = adapter.fetch_by_case_number("00000010020248090001")

    assert result.hits[0].source_id == "fixture-1"
