"""Tests for the bounded, sanitized S5 thematic catalog probe."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agrojud.config import Settings
from agrojud.sources import DataJudSourceAdapter
from agrojud.sources.catalog import load_catalog
from agrojud.sources.catalog_validation import (
    MAX_REQUESTS_PER_ITEM,
    PAGE_SIZE,
    CatalogValidationProbe,
    main,
)
from agrojud.sources.datajud_validation import write_report

API_KEY = "do-not-leak-catalog-probe-key"
DATABASE_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_demo"
TEST_DATABASE_URL = "postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_probe_test"
FILED_FROM = date(2026, 5, 1)
FILED_TO = date(2026, 6, 1)


def make_settings() -> Settings:
    return Settings(
        environment="test",
        database_url=DATABASE_URL,
        test_database_url=TEST_DATABASE_URL,
        datajud_api_key=SecretStr(API_KEY),
    )


def item(item_id: str) -> Any:
    return next(entry for entry in load_catalog().items if entry.id == item_id)


def hit(
    index: int,
    *,
    subjects: list[object] | None = None,
    movement: int | None = None,
    class_code: int = 7,
) -> dict[str, object]:
    source_id = f"TJGO_100_G1_1_1000000000000000000{index}"
    return {
        "_id": source_id,
        "_source": {
            "id": source_id,
            "numeroProcesso": f"1000000000000000000{index}",
            "tribunal": "TJGO",
            "classe": {"codigo": class_code},
            "assuntos": subjects or [],
            "movimentos": [{"codigo": movement}] if movement is not None else [],
        },
        "sort": [1_700_000_000_000 + index, source_id],
    }


def body(hits: list[dict[str, object]], total: int | None = None) -> dict[str, object]:
    return {"hits": {"total": {"value": total or len(hits), "relation": "eq"}, "hits": hits}}


def run_probe(
    handler: Any, *item_ids: str
) -> tuple[dict[str, object], list[httpx.Request], list[float]]:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)  # type: ignore[no-any-return]

    adapter = DataJudSourceAdapter(make_settings(), transport=httpx.MockTransport(recording))
    with adapter:
        report = CatalogValidationProbe(
            adapter,
            filed_from=FILED_FROM,
            filed_to=FILED_TO,
            items=[item(item_id) for item_id in item_ids],
            sleep=sleeps.append,
        ).run()
    return report, requests, sleeps


def result(report: dict[str, object], index: int = 0) -> dict[str, Any]:
    return report["items"][index]  # type: ignore[index, no-any-return]


def test_subject_preset_with_three_matching_documents_is_validated() -> None:
    hits = [hit(i, subjects=[{"codigo": 10501}, {"codigo": 99}]) for i in range(1, 4)]
    hits.append(hit(4, subjects=[[{"codigo": 4964}]]))  # nested list shape observed in DataJud
    report, requests, _ = run_probe(
        lambda _: httpx.Response(200, json=body(hits, 40)), "rural.credito_contratos"
    )

    entry = result(report)
    assert len(requests) == 1
    sent = json.loads(requests[0].content)
    assert {"terms": {"assuntos.codigo": [4964, 4976, 10501]}} in sent["query"]["bool"]["filter"]
    assert sent["size"] == PAGE_SIZE
    assert entry["total"] == {"value": 40, "relation": "eq"}
    assert entry["query_status"]["state"] == "VALIDATED"
    assert entry["query_status"]["evidence"]["matched_codes"] == {
        "subject": {"4964": 1, "10501": 3}
    }
    assert entry["sample_status"]["state"] == "VALIDATED"
    assert entry["sample_status"]["evidence"]["distinct_matching_documents"] == 4


def test_hit_without_requested_code_is_incompatible() -> None:
    hits = [hit(1, movement=11382), hit(2, movement=11382), hit(3, movement=26)]
    report, _, _ = run_probe(lambda _: httpx.Response(200, json=body(hits)), "sinal.penhora")

    entry = result(report)
    assert entry["query_status"]["state"] == "INCOMPATIBLE"
    assert entry["query_status"]["evidence"]["mismatched_hits"] == 1
    assert entry["sample_status"]["state"] == "INCONCLUSIVE"


def test_empty_result_is_inconclusive_not_absence() -> None:
    report, _, _ = run_probe(lambda _: httpx.Response(200, json=body([])), "sinal.leilao")

    entry = result(report)
    assert entry["query_status"]["state"] == "INCONCLUSIVE"
    assert entry["sample_status"]["state"] == "INCONCLUSIVE"
    assert "ausência não prova" in entry["query_status"]["evidence"]["reason"]


def test_full_page_with_too_few_examples_fetches_one_more_page_at_most() -> None:
    full_page = [hit(1, class_code=129)] * PAGE_SIZE
    report, requests, sleeps = run_probe(
        lambda _: httpx.Response(200, json=body(full_page, 5_000)),
        "recuperacao_judicial.por_classe",
    )

    entry = result(report)
    assert len(requests) == MAX_REQUESTS_PER_ITEM
    second = json.loads(requests[1].content)
    assert len(second["search_after"]) == 2
    assert entry["requests"][1]["query"]["search_after"] == ["<cursor-redigido>"] * 2
    assert entry["query_status"]["state"] == "VALIDATED"
    assert entry["sample_status"]["state"] == "INCONCLUSIVE"
    assert sleeps == [1.0]


def test_timeout_keeps_item_inconclusive_and_probe_continues() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "11382" in request.content.decode():
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json=body([hit(i, movement=311) for i in range(1, 4)]))

    report, requests, _ = run_probe(handler, "sinal.penhora", "sinal.leilao")

    assert len(requests) == 2
    assert result(report, 0)["query_status"]["state"] == "INCONCLUSIVE"
    assert result(report, 0)["requests"][0]["error_code"] == "NETWORK"
    assert result(report, 1)["sample_status"]["state"] == "VALIDATED"
    assert report["stopped_reason"] is None


@pytest.mark.parametrize("status", [401, 429])
def test_authentication_or_rate_limit_stops_without_retry(status: int) -> None:
    report, requests, _ = run_probe(
        lambda _: httpx.Response(status, headers={"Retry-After": "5"}),
        "sinal.penhora",
        "sinal.leilao",
    )

    assert len(requests) == 1
    assert report["stopped_reason"] is not None
    assert result(report, 1)["requests"] == []
    assert result(report, 1)["query_status"]["state"] == "INCONCLUSIVE"


def test_report_does_not_leak_case_numbers_ids_or_key(tmp_path: Path) -> None:
    hits = [hit(i, subjects=[{"codigo": 10501}]) for i in range(1, 4)]
    report, _, _ = run_probe(
        lambda _: httpx.Response(200, json=body(hits)), "rural.credito_contratos"
    )
    output = tmp_path / "s5.json"
    write_report(report, output)
    text = output.read_text(encoding="utf-8")

    assert API_KEY not in text
    assert "1000000000000000000" not in text
    assert "TJGO_100_G1" not in text


def test_window_is_bounded() -> None:
    adapter = DataJudSourceAdapter(
        make_settings(), transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    with adapter, pytest.raises(ValueError, match="31 dias"):
        CatalogValidationProbe(
            adapter, filed_from=date(2026, 1, 1), filed_to=date(2026, 3, 1), items=[]
        )


def test_cli_refuses_without_key_or_real_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "s5.json"
    monkeypatch.delenv("DATAJUD_API_KEY", raising=False)
    assert main(["--output", str(output)]) == 2
    monkeypatch.setenv("DATAJUD_API_KEY", API_KEY)
    assert main(["--output", str(output)]) == 2
    assert "AGROJUD_ENV=real" in capsys.readouterr().err
    assert not output.exists()
    with pytest.raises(SystemExit):
        main(["--output", str(output), "--item", "desconhecido"])
