"""Tests for the bounded, sanitized SPEC-026 DataJud page probe."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import SourceRateLimit
from agrojud.services.collection import build_collection_request
from agrojud.services.jobs import JobService
from agrojud.sources.contracts import (
    SourceError,
    SourceErrorCode,
    SourceHit,
    SourcePage,
    SourceQuery,
)
from agrojud.sources.datajud import DataJudProbeResponse
from agrojud.sources.datajud_page_probe import (
    DataJudPageSizeProbe,
    main,
    runtime_readiness_reason,
)

OPERATIONAL_URL = "postgresql+psycopg://agrojud:secret@localhost:55432/agrojud_demo"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeAdapter:
    def __init__(self, responses: list[DataJudProbeResponse | SourceError]) -> None:
        self.responses = responses
        self.calls: list[tuple[SourceQuery, object, int]] = []

    def fetch_probe_page(
        self,
        query: SourceQuery,
        cursor: object,
        page_size: int,
    ) -> DataJudProbeResponse:
        self.calls.append((query, cursor, page_size))
        response = self.responses[len(self.calls) - 1]
        if isinstance(response, SourceError):
            raise response
        return response


def make_page(start: int, count: int) -> SourcePage:
    hits = tuple(
        SourceHit(
            source_id=f"TJGO_{index}",
            source={"tribunal": "TJGO"},
            sort_values=(index, f"TJGO_{index}"),
            raw={},
        )
        for index in range(start, start + count)
    )
    cursor = hits[-1].sort_values if hits else None
    return SourcePage(
        hits=hits,
        total_value=10_000,
        total_relation="gte",
        cursor_final=cursor,
        responded_at=datetime.now(UTC),
    )


def make_response(start: int, count: int, response_bytes: int = 1234) -> DataJudProbeResponse:
    return DataJudProbeResponse(
        page=make_page(start, count),
        status_code=200,
        response_bytes=response_bytes,
    )


def make_query() -> SourceQuery:
    return SourceQuery(
        tribunal="TJGO",
        filed_from=date(2026, 5, 1),
        filed_to=date(2026, 6, 1),
        class_codes=(1116, 12154),
        preset_id="execucoes.amplas",
        preset_version="1.0.0",
    )


def run_probe(
    adapter: FakeAdapter,
    *,
    readiness_reason: str | None = None,
) -> tuple[dict[str, Any], FakeClock]:
    clock = FakeClock()
    probe = DataJudPageSizeProbe(
        adapter,  # type: ignore[arg-type]
        make_query(),
        readiness_check=lambda: readiness_reason,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    return probe.run(), clock


def test_validates_full_page_and_nonempty_search_after_continuation() -> None:
    adapter = FakeAdapter([make_response(0, 10_000), make_response(10_000, 7)])

    report, clock = run_probe(adapter)

    assert report["state"] == "VALIDATED"
    assert report["reason_code"] is None
    assert report["request_budget"]["requests_made"] == 2
    requests = report["requests"]
    assert [record["hit_count"] for record in requests] == [10_000, 7]
    assert [record["response_bytes"] for record in requests] == [1234, 1234]
    assert requests[0]["cursor_sha256"] != requests[1]["cursor_sha256"]
    assert adapter.calls[0][1] is None
    assert adapter.calls[1][1] == make_page(0, 10_000).cursor_final
    assert adapter.calls[0][0] is adapter.calls[1][0]
    assert [page_size for _, _, page_size in adapter.calls] == [10_000, 10_000]
    assert clock.sleeps == [0.5, 0.5]
    serialized = json.dumps(report)
    assert "TJGO_9999" not in serialized


@pytest.mark.parametrize(
    ("first_page", "reason"),
    [
        (make_response(0, 9_999), "FIRST_PAGE_DID_NOT_RETURN_10000_HITS"),
        (make_response(0, 10_000), "FIRST_PAGE_CURSOR_MISSING"),
    ],
)
def test_inconclusive_first_page_never_sends_continuation(
    first_page: DataJudProbeResponse, reason: str
) -> None:
    if reason == "FIRST_PAGE_CURSOR_MISSING":
        first_page = DataJudProbeResponse(
            page=SourcePage(
                hits=first_page.page.hits,
                total_value=10_000,
                total_relation="gte",
                cursor_final=None,
                responded_at=first_page.page.responded_at,
            ),
            status_code=200,
            response_bytes=first_page.response_bytes,
        )
    adapter = FakeAdapter([first_page])

    report, _ = run_probe(adapter)

    assert report["state"] == "INCONCLUSIVE"
    assert report["reason_code"] == reason
    assert len(adapter.calls) == 1


def test_upstream_failure_is_recorded_without_retry_or_empty_success() -> None:
    adapter = FakeAdapter(
        [
            SourceError(
                SourceErrorCode.RATE_LIMIT,
                "rejected",
                status_code=429,
                retry_after="30",
            )
        ]
    )

    report, _ = run_probe(adapter)

    assert report["state"] == "INCONCLUSIVE"
    assert report["reason_code"] == "UPSTREAM_RATE_LIMIT"
    assert report["requests"][0]["http_status"] == 429
    assert report["requests"][0]["outcome"] == "ERROR"
    assert len(adapter.calls) == 1


@pytest.mark.parametrize(
    ("second_page", "reason"),
    [
        (make_response(10_000, 0), "CONTINUATION_PAGE_EMPTY"),
        (make_response(10_000, 7), "CURSOR_DID_NOT_ADVANCE"),
    ],
)
def test_empty_or_repeated_cursor_continuation_is_not_validated(
    second_page: DataJudProbeResponse,
    reason: str,
) -> None:
    if reason == "CURSOR_DID_NOT_ADVANCE":
        first_cursor = make_page(0, 10_000).cursor_final
        assert first_cursor is not None
        second_page = DataJudProbeResponse(
            page=SourcePage(
                hits=second_page.page.hits,
                total_value=10_000,
                total_relation="gte",
                cursor_final=first_cursor,
                responded_at=second_page.page.responded_at,
            ),
            status_code=200,
            response_bytes=second_page.response_bytes,
        )
    adapter = FakeAdapter([make_response(0, 10_000), second_page])

    report, _ = run_probe(adapter)

    assert report["state"] == "INCONCLUSIVE"
    assert report["reason_code"] == reason
    assert len(adapter.calls) == 2


def test_readiness_gate_blocks_http_before_any_request() -> None:
    adapter = FakeAdapter([make_response(0, 10_000)])

    report, clock = run_probe(adapter, readiness_reason="DATAJUD_JOB_ACTIVE_OR_READY")

    assert report["state"] == "INCONCLUSIVE"
    assert report["reason_code"] == "DATAJUD_JOB_ACTIVE_OR_READY"
    assert report["requests"] == []
    assert adapter.calls == []
    assert clock.sleeps == []


def test_cli_requires_explicit_terms_review_before_loading_live_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "probe.json"
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    monkeypatch.setattr(
        "agrojud.sources.datajud_page_probe.get_settings",
        lambda: pytest.fail("live settings must not be loaded before terms confirmation"),
    )

    result = main(
        [
            "--from-date",
            "2026-05-01",
            "--through-date",
            "2026-05-31",
            "--output",
            str(output),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert result == 2
    assert report["state"] == "INCONCLUSIVE"
    assert report["reason_code"] == "TERMS_REVIEW_REQUIRED"
    assert report["terms_review"]["operator_confirmed_before_probe"] is False
    assert report["request_budget"]["requests_made"] == 0


@pytest.fixture
def readonly_probe_settings(scratch_database_url: URL) -> Iterator[Settings]:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    settings = Settings(
        environment="test",
        database_url=OPERATIONAL_URL,
        test_database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    yield settings
    engine.dispose()


def test_runtime_preflight_requires_no_active_ready_or_claimed_datajud_jobs(
    readonly_probe_settings: Settings,
) -> None:
    request = build_collection_request(
        mode="real",
        source="datajud",
        job_type="discovery",
        query=SourceQuery(
            tribunal="TJGO",
            filed_from=date(2026, 1, 1),
            filed_to=date(2026, 1, 2),
        ),
        page_size=100,
    )
    engine = make_engine(readonly_probe_settings.effective_database_url)
    try:
        jobs = JobService(sessionmaker(engine, expire_on_commit=False))
        jobs.enqueue(request)
    finally:
        engine.dispose()

    assert runtime_readiness_reason(readonly_probe_settings) == "DATAJUD_JOB_ACTIVE_OR_READY"


def test_runtime_preflight_blocks_a_persisted_source_cooldown(
    readonly_probe_settings: Settings,
) -> None:
    from datetime import timedelta

    engine = make_engine(readonly_probe_settings.effective_database_url)
    try:
        with Session(engine) as session, session.begin():
            session.add(
                SourceRateLimit(
                    source="datajud",
                    next_request_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
    finally:
        engine.dispose()

    assert runtime_readiness_reason(readonly_probe_settings) == (
        "DATAJUD_RATE_LIMIT_OR_COOLDOWN_ACTIVE"
    )
