"""PostgreSQL-backed HTTP contract tests for SPEC-011."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from agrojud.api.app import create_app
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    Base,
    Process,
    Representation,
    RepresentationSubject,
)
from agrojud.services.collection import build_collection_job_handler
from agrojud.services.job_worker import LeasedWorker

PRESET_ID = "rural.credito_contratos"
DEFAULT_JOB_REQUEST = {
    "kind": "discovery",
    "criteria": {"preset_id": PRESET_ID},
}


@pytest.fixture
def api_app(scratch_database_url: URL) -> Iterator[FastAPI]:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    settings = Settings(
        environment="demo",
        database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    app = create_app(settings, engine)
    yield app
    engine.dispose()


@pytest.fixture
def api_client(api_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(api_app, raise_server_exceptions=False) as client:
        yield client


def test_http_collection_flow_is_read_only_and_hides_source_cursor(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    created = api_client.post("/api/v1/jobs", json=DEFAULT_JOB_REQUEST)
    assert created.status_code == 202
    body = created.json()
    job_id = UUID(body["job_id"])
    assert body["status"] == "queued"
    assert body["reused"] is False
    assert created.headers["location"].endswith(f"/api/v1/jobs/{job_id}")

    worker = LeasedWorker(
        api_app.state.jobs,
        worker_id="api-spec-test-worker",
        handlers={
            "discovery": build_collection_job_handler(api_app.state.settings),
            "refresh_number": build_collection_job_handler(api_app.state.settings),
        },
        heartbeat_interval=1,
    )
    assert worker.run_once()

    counts_before_gets = _domain_counts(api_app.state.database_engine)
    job = api_client.get(f"/api/v1/jobs/{job_id}")
    assert job.status_code == 200
    job_body = job.json()
    assert job_body["status"] == "completed"
    assert job_body["criteria"]["preset_id"] == PRESET_ID
    assert job_body["coverage"]["source_total"] == {
        "present": True,
        "value": 1,
        "relation": "eq",
    }
    assert "cursor" not in job_body["checkpoint"]

    listed_jobs = api_client.get("/api/v1/jobs?page=1&page_size=10")
    assert listed_jobs.status_code == 200
    assert listed_jobs.json()["total"] == 1
    assert listed_jobs.json()["items"][0]["id"] == str(job_id)

    number = "0000001-00.2026.8.09.0001"
    processes = api_client.get("/api/v1/processes", params={"process_number": number})
    assert processes.status_code == 200
    process_page = processes.json()
    assert process_page["total"] == 1
    process = process_page["items"][0]
    assert process["numero_cnj"] == "00000010020268090001"
    process_id = process["id"]
    assert process["latest_collection"]["environment"] == "demo"
    assert process["latest_collection"]["job_status"] == "completed"

    by_collection = api_client.get(
        "/api/v1/processes",
        params={"collection_id": body["collection_id"]},
    )
    by_preset = api_client.get("/api/v1/processes", params={"preset_id": PRESET_ID})
    assert by_collection.status_code == by_preset.status_code == 200
    assert by_collection.json()["total"] == by_preset.json()["total"] == 1

    subject_options = api_client.get(
        "/api/v1/process-filter-options",
        params={"field": "subject_name_exact", "q": "ASSÚNTO TPU"},
    )
    collection_options = api_client.get(
        "/api/v1/process-filter-options",
        params={"field": "collection_id", "selected_value": body["collection_id"]},
    )
    number_options = api_client.get(
        "/api/v1/process-filter-options",
        params={"field": "process_number", "q": "0000001-00.2026"},
    )
    assert (
        subject_options.status_code
        == collection_options.status_code
        == number_options.status_code
        == 200
    )
    assert subject_options.json()["items"][0]["value"] == "Assunto TPU 10501"
    assert number_options.json()["items"][0]["value"] == "00000010020268090001"
    selected_collection = collection_options.json()["items"][0]
    assert selected_collection["value"] == body["collection_id"]
    assert PRESET_ID in selected_collection["label"]
    assert "Concluída" in selected_collection["label"]
    assert body["collection_id"] not in selected_collection["label"]

    detail = api_client.get(f"/api/v1/processes/{process_id}")
    assert detail.status_code == 200
    representation_page = api_client.get(
        f"/api/v1/processes/{process_id}/representations",
        params={"page": 1, "page_size": 10},
    )
    assert representation_page.status_code == 200
    representation = representation_page.json()["items"][0]
    assert representation["source_filed_at_original"] is not None
    assert representation["movement_diagnostic"]["available"] is True
    assert representation["movement_diagnostic"]["is_complete"] is True
    assert representation["latest_collection"]["collection_id"] == body["collection_id"]

    movements = api_client.get(f"/api/v1/processes/{process_id}/movements")
    assert movements.status_code == 200
    movement_body = movements.json()
    assert movement_body["total"] == 1
    assert movement_body["items"][0]["representation_id"] == representation["id"]
    assert "raw_movement" not in movement_body["items"][0]
    assert movement_body["diagnostics"][0]["is_complete"] is True

    environment = api_client.get("/api/v1/environment")
    presets = api_client.get("/api/v1/presets")
    live = api_client.get("/api/v1/health/live")
    ready = api_client.get("/api/v1/health/ready")
    assert environment.status_code == 200
    assert environment.json()["source"] == "synthetic"
    assert presets.status_code == 200
    assert next(item for item in presets.json()["items"] if item["id"] == PRESET_ID)[
        "availability"
    ]["enabled"]
    assert live.status_code == 200
    assert ready.status_code == 200
    assert _domain_counts(api_app.state.database_engine) == counts_before_gets


def test_equivalent_jobs_are_reused_and_errors_have_one_shape(api_client: TestClient) -> None:
    first = api_client.post("/api/v1/jobs", json=DEFAULT_JOB_REQUEST)
    second = api_client.post("/api/v1/jobs", json=DEFAULT_JOB_REQUEST)
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["reused"] is False
    assert second.json()["reused"] is True

    missing = api_client.get(f"/api/v1/jobs/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert missing.json()["error"]["request_id"] == missing.headers["x-request-id"]

    invalid = api_client.post(
        "/api/v1/jobs",
        json={"kind": "discovery", "criteria": {"preset_id": "unknown.preset"}},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_input"
    assert invalid.json()["error"]["details"]
    assert invalid.json()["error"]["request_id"] == invalid.headers["x-request-id"]

    invalid_page = api_client.get("/api/v1/jobs", params={"page_size": 101})
    assert invalid_page.status_code == 422
    assert invalid_page.json()["error"]["request_id"] == invalid_page.headers["x-request-id"]

    openapi = api_client.get("/api/v1/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    assert "/api/v1/jobs" in paths
    assert "/api/v1/processes/{process_id}/movements" in paths
    assert "ErrorResponse" in openapi.json()["components"]["schemas"]


def test_concurrent_equivalent_job_creations_share_one_persisted_job(api_app: FastAPI) -> None:
    start = Barrier(2)

    def submit() -> dict[str, object]:
        with TestClient(api_app, raise_server_exceptions=False) as client:
            start.wait(timeout=5)
            response = client.post("/api/v1/jobs", json=DEFAULT_JOB_REQUEST)
            assert response.status_code == 202
            return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: submit(), range(2)))
    assert len({result["job_id"] for result in results}) == 1
    assert sum(result["reused"] is False for result in results) == 1
    assert sum(result["reused"] is True for result in results) == 1
    with Session(api_app.state.database_engine) as session:
        assert session.scalar(select(func.count()).select_from(Representation)) == 0
        assert session.scalar(select(func.count()).select_from(Process)) == 0


def test_continue_is_available_over_http_after_a_confirmed_budget_limit(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    created = api_client.post(
        "/api/v1/jobs",
        json={"kind": "discovery", "criteria": {"preset_id": PRESET_ID, "hit_budget": 1}},
    )
    assert created.status_code == 202
    job_id = UUID(created.json()["job_id"])
    worker = LeasedWorker(
        api_app.state.jobs,
        worker_id="api-continue-test-worker",
        handlers={"discovery": build_collection_job_handler(api_app.state.settings)},
        heartbeat_interval=1,
    )
    assert worker.run_once()
    limited = api_client.get(f"/api/v1/jobs/{job_id}").json()
    assert limited["status"] == "partial"
    assert limited["reason"] == "limit"

    continued = api_client.post(f"/api/v1/jobs/{job_id}/continue")
    assert continued.status_code == 202
    assert continued.json()["job_id"] == str(job_id)
    assert continued.json()["status"] == "queued"
    assert continued.headers["location"].endswith(f"/api/v1/jobs/{job_id}")
    assert worker.run_once()
    completed = api_client.get(f"/api/v1/jobs/{job_id}").json()
    assert completed["status"] == "completed"
    assert completed["coverage"]["budget_limit"] == 2_001
    assert completed["coverage"]["continuations"] == 1


def test_restart_scan_is_enqueued_over_http_only_for_an_invalid_cursor(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    created = api_client.post("/api/v1/jobs", json=DEFAULT_JOB_REQUEST)
    previous_id = UUID(created.json()["job_id"])
    lease = api_app.state.jobs.claim("api-restart-test-worker")
    assert lease is not None
    api_app.state.jobs.finish(
        previous_id,
        lease.possession_token,
        status="failed",
        reason="cursor_invalid",
        cursor_invalid=True,
    )

    restarted = api_client.post(f"/api/v1/jobs/{previous_id}/restart-scan")
    assert restarted.status_code == 202
    assert restarted.json()["job_id"] != str(previous_id)
    assert restarted.json()["status"] == "queued"
    current = api_client.get(f"/api/v1/jobs/{restarted.json()['job_id']}")
    assert current.status_code == 200
    assert current.json()["predecessor_job_id"] == str(previous_id)


def test_refresh_number_is_normalized_and_executed_from_http(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    process_id = uuid4()
    with Session(api_app.state.database_engine) as session, session.begin():
        session.add(Process(id=process_id, numero_cnj="00000010020268090001"))
    included = api_client.put(f"/api/v1/processes/{process_id}/watch")
    assert included.status_code == 200

    created = api_client.post(
        "/api/v1/jobs",
        json={
            "kind": "refresh_number",
            "criteria": {"process_number": "0000001-00.2026.8.09.0001"},
        },
    )
    assert created.status_code == 202
    job_id = UUID(created.json()["job_id"])
    detail = api_client.get(f"/api/v1/jobs/{job_id}")
    assert detail.json()["criteria"]["process_number"] == "00000010020268090001"
    worker = LeasedWorker(
        api_app.state.jobs,
        worker_id="api-refresh-test-worker",
        handlers={"refresh_number": build_collection_job_handler(api_app.state.settings)},
        heartbeat_interval=1,
    )
    assert worker.run_once()
    result = api_client.get(
        "/api/v1/processes",
        params={"process_number": "00000010020268090001"},
    )
    assert result.status_code == 200
    assert result.json()["total"] == 1
    assert result.json()["items"][0]["numero_cnj"] == "00000010020268090001"


def test_job_commands_follow_persisted_transitions_and_reject_incompatible_actions(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    created = api_client.post("/api/v1/jobs", json=DEFAULT_JOB_REQUEST)
    job_id = UUID(created.json()["job_id"])

    cancelled = api_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    resumed = api_client.post(f"/api/v1/jobs/{job_id}/resume")
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "queued"

    claimed = api_app.state.jobs.claim("api-cancel-transition-worker")
    assert claimed is not None
    running_cancel = api_client.post(f"/api/v1/jobs/{job_id}/cancel")
    assert running_cancel.status_code == 200
    assert running_cancel.json()["status"] == "running"
    assert running_cancel.json()["cancel_requested"] is True
    api_app.state.jobs.finish(job_id, claimed.possession_token, status="completed")
    assert api_client.get(f"/api/v1/jobs/{job_id}").json()["status"] == "cancelled"

    resumed_again = api_client.post(f"/api/v1/jobs/{job_id}/resume")
    assert resumed_again.status_code == 202
    worker = LeasedWorker(
        api_app.state.jobs,
        worker_id="api-command-test-worker",
        handlers={"discovery": build_collection_job_handler(api_app.state.settings)},
        heartbeat_interval=1,
    )
    assert worker.run_once()
    completed = api_client.get(f"/api/v1/jobs/{job_id}").json()
    assert completed["status"] == "completed"

    counts_before_conflicts = _domain_counts(api_app.state.database_engine)
    for action in ("cancel", "resume", "continue", "restart-scan"):
        conflict = api_client.post(f"/api/v1/jobs/{job_id}/{action}")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["request_id"] == conflict.headers["x-request-id"]
    assert _domain_counts(api_app.state.database_engine) == counts_before_conflicts


def test_real_source_is_enabled_with_key_and_presets_follow_s5_evidence(
    api_app: FastAPI,
) -> None:
    settings = Settings(
        environment="real",
        database_url=api_app.state.settings.database_url,
        datajud_api_key=SecretStr("real-key-for-test"),
    )
    app = create_app(settings, api_app.state.database_engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        environment = client.get("/api/v1/environment")
        assert environment.status_code == 200
        assert environment.json() == {
            "environment": "real",
            "source": "datajud",
            "source_enabled": True,
            "source_disabled_reason": None,
        }

        presets = client.get("/api/v1/presets")
        assert presets.status_code == 200
        items = presets.json()["items"]
        assert items
        assert all(item["availability"]["enabled"] for item in items)
        assert all(item["availability"]["label"] == "habilitado no real" for item in items)

        accepted = client.post("/api/v1/jobs", json=DEFAULT_JOB_REQUEST)
        assert accepted.status_code == 202, accepted.text
        job = client.get(f"/api/v1/jobs/{accepted.json()['job_id']}")
        assert job.json()["environment"] == "real"
        assert job.json()["source"] == "datajud"


def test_real_source_without_key_is_disabled_with_reason(api_app: FastAPI) -> None:
    settings = Settings(
        environment="real",
        database_url=api_app.state.settings.database_url,
        datajud_api_key=None,
    )
    app = create_app(settings, api_app.state.database_engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        environment = client.get("/api/v1/environment")
        assert environment.status_code == 200
        assert environment.json()["source_enabled"] is False
        assert "DATAJUD_API_KEY" in environment.json()["source_disabled_reason"]

        blocked = client.post(
            "/api/v1/jobs",
            json={
                "kind": "refresh_number",
                "criteria": {"process_number": "00000010020268090001"},
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "feature_unavailable"


def test_process_filters_match_one_representation_and_keep_processes_unique(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    with Session(api_app.state.database_engine) as session, session.begin():
        split_id = _seed_process(
            session,
            process_number="00000030020268090001",
            representations=(
                ("Execução", "Direito civil"),
                ("Cobrança", "Crédito Rural"),
            ),
        )
        matching_id = _seed_process(
            session,
            process_number="00000040020268090001",
            representations=(
                ("Execução", "Crédito Rural"),
                ("Execução", "Crédito Rural"),
            ),
        )

    matching = api_client.get(
        "/api/v1/processes",
        params={"subject": "crédito", "class": "execução"},
    )
    assert matching.status_code == 200
    matching_body = matching.json()
    assert matching_body["total"] == 1
    assert [item["id"] for item in matching_body["items"]] == [str(matching_id)]

    split_only = api_client.get(
        "/api/v1/processes",
        params={
            "subject": "crédito",
            "class": "execução",
            "process_number": "0000003-00.2026.8.09.0001",
        },
    )
    assert split_only.status_code == 200
    assert split_only.json()["items"] == []
    assert split_only.json()["total"] == 0

    beyond_end = api_client.get("/api/v1/processes", params={"page": 4, "page_size": 1})
    assert beyond_end.status_code == 200
    assert beyond_end.json()["items"] == []
    assert beyond_end.json()["total"] == 2
    assert split_id != matching_id


def test_process_filter_options_match_accents_and_ignore_other_active_filters(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    with Session(api_app.state.database_engine) as session, session.begin():
        _seed_process(
            session,
            process_number="00000050020268090001",
            representations=(("Ação de cobrança", "Execução rural"),),
        )

    options = api_client.get(
        "/api/v1/process-filter-options",
        params={"field": "subject_name_exact", "q": "EXECUCAO", "decision": "relevant"},
    )
    assert options.status_code == 200
    assert options.json()["items"] == [
        {
            "value": "Execução rural",
            "label": "Execução rural",
            "detail": None,
            "process_count": 1,
        }
    ]


def test_process_filter_options_cap_default_search_and_cnj_results(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    with Session(api_app.state.database_engine) as session, session.begin():
        for index in range(25):
            _seed_process(
                session,
                process_number=f"{index + 1:07d}0020268090001",
                representations=(("Classe", f"Tema persistido {index:02d}"),),
            )

    common = api_client.get(
        "/api/v1/process-filter-options", params={"field": "subject_name_exact"}
    )
    searched = api_client.get(
        "/api/v1/process-filter-options",
        params={"field": "subject_name_exact", "q": "tema persistido"},
    )
    cnj_default = api_client.get(
        "/api/v1/process-filter-options", params={"field": "process_number"}
    )
    assert common.status_code == searched.status_code == cnj_default.status_code == 200
    assert len(common.json()["items"]) == 10
    assert len(searched.json()["items"]) == 20
    assert cnj_default.json()["items"] == []


def test_unavailable_database_returns_sanitized_503(scratch_database_url: URL) -> None:
    settings = Settings(
        environment="demo",
        database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    unavailable_engine = make_engine(
        "postgresql+psycopg://agrojud:secret@127.0.0.1:1/agrojud_unavailable_test?connect_timeout=1"
    )
    app = create_app(settings, unavailable_engine)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/jobs")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
        assert "secret" not in response.text
        assert "127.0.0.1" not in response.text
    finally:
        unavailable_engine.dispose()


def _domain_counts(engine: Engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            table.name: int(session.scalar(select(func.count()).select_from(table)) or 0)
            for table in Base.metadata.sorted_tables
        }


def _seed_process(
    session: Session,
    *,
    process_number: str,
    representations: tuple[tuple[str, str], ...],
) -> UUID:
    process_id = uuid4()
    process = Process(id=process_id, numero_cnj=process_number)
    session.add(process)
    session.flush()
    for index, (class_name, subject_name) in enumerate(representations, start=1):
        representation_id = uuid4()
        session.add(
            Representation(
                id=representation_id,
                process_id=process_id,
                source="synthetic",
                tribunal="TJGO",
                source_id=f"seed-{process_number}-{index}",
                class_code=str(index),
                class_name=class_name,
                court_unit_code="1",
                court_unit_name="Unidade de teste",
            )
        )
        session.flush()
        session.add(
            RepresentationSubject(
                id=uuid4(),
                representation_id=representation_id,
                ordinal=1,
                subject_code=str(index),
                subject_name=subject_name,
            )
        )
    return process_id
