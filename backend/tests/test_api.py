"""PostgreSQL-backed HTTP contract tests for SPEC-011."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from agrojud.api.app import create_app
from agrojud.api.schemas import DiscoveryCriteria, RefreshNumberCriteria, SavedSearchFilters
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    Base,
    Job,
    Process,
    Representation,
    RepresentationSubject,
)
from agrojud.services.collection import build_collection_job_handler
from agrojud.services.job_worker import LeasedWorker
from agrojud.sources.contracts import DEFAULT_PAGE_SIZE

PRESET_ID = "rural.credito_contratos"
DEFAULT_JOB_REQUEST = {
    "kind": "discovery",
    "criteria": {"preset_id": PRESET_ID},
}


@pytest.mark.parametrize(
    "criteria_type", [DiscoveryCriteria, RefreshNumberCriteria, SavedSearchFilters]
)
def test_collection_interfaces_share_page_bounds_and_unlimited_budget_default(
    criteria_type: type[DiscoveryCriteria] | type[RefreshNumberCriteria] | type[SavedSearchFilters],
) -> None:
    base: dict[str, object] = {}
    if criteria_type is DiscoveryCriteria:
        base["preset_id"] = PRESET_ID
    elif criteria_type is RefreshNumberCriteria:
        base["process_number"] = "00000010020248090001"
    for page_size in (10, 10_000):
        parsed = criteria_type.model_validate({**base, "page_size": page_size})
        assert parsed.page_size == page_size
        assert parsed.hit_budget is None
    for page_size in (9, 10_001):
        with pytest.raises(ValidationError):
            criteria_type.model_validate({**base, "page_size": page_size})
    defaults = criteria_type.model_validate(base)
    assert defaults.page_size == DEFAULT_PAGE_SIZE
    assert defaults.hit_budget is None


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
    assert "Crédito e contratos rurais" in selected_collection["label"]
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
    assert "Assunto 4964 — Cédula de Crédito Rural" in representation["discovery_reasons"][0]
    assert "Assunto 10501 — Crédito Rural" in representation["discovery_reasons"][0]

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
    assert "/api/v1/search-types" in paths
    assert "/api/v1/tpu-codes" in paths
    assert "/api/v1/custom-searches" in paths
    assert "/api/v1/processes/{process_id}/movements" in paths
    assert "ErrorResponse" in openapi.json()["components"]["schemas"]


def test_official_themes_and_tpu_picker_expose_only_current_approved_codes(
    api_client: TestClient,
) -> None:
    themes = api_client.get("/api/v1/search-types")
    assert themes.status_code == 200
    items = {item["id"]: item for item in themes.json()["items"]}
    assert {item["name"] for item in items.values()} == {
        "Crédito e contratos rurais",
        "Recuperação judicial",
        "Execuções relacionadas ao agro",
    }
    assert items["tema.recuperacao_judicial"]["criteria"] == {
        "classes": [129],
        "subjects": [13277],
        "movements": [],
    }
    assert items["tema.recuperacao_judicial"]["combinations"]["between_dimensions"] == "OR"
    assert (
        items["tema.execucoes_relacionadas_ao_agro"]["combinations"]["between_dimensions"] == "AND"
    )

    active_subjects = api_client.get("/api/v1/tpu-codes", params={"dimension": "subject"})
    active_classes = api_client.get("/api/v1/tpu-codes", params={"dimension": "class"})
    assert {item["code"] for item in active_subjects.json()} == {4964, 4976, 10501, 13277}
    assert {item["code"] for item in active_classes.json()} == {
        129,
        159,
        155,
        156,
        1116,
        12078,
        12079,
        12154,
    }
    assert active_subjects.json()[0]["name"]
    assert api_client.get("/api/v1/tpu-codes", params={"dimension": "unknown"}).status_code == 422


def test_custom_search_create_duplicate_version_archive_and_pinned_saved_history(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    created = api_client.post(
        "/api/v1/custom-searches",
        json={
            "name": "Recuperação com evidência",
            "description": "Versão inicial",
            "class_codes": [129],
            "subject_codes": [13277],
            "dimension_combination": "OR",
        },
    )
    assert created.status_code == 201, created.text
    original = created.json()
    search_id = original["id"]
    assert original["version"] == 1

    saved = api_client.post(
        "/api/v1/saved-searches",
        json={
            "name": "Busca que preserva a versão",
            "search_definition": {
                "kind": "custom_search",
                "id": search_id,
                "version": 1,
            },
            "window_mode": "fixed",
            "filters": {
                "tribunals": ["TJGO"],
                "filed_from": "2025-01-01",
                "filed_through": "2025-02-01",
                "page_size": 100,
                "hit_budget": 100,
            },
            "enabled": False,
        },
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["search_definition"] == {
        "kind": "custom_search",
        "id": search_id,
        "version": 1,
    }
    assert saved.json()["definition_name"] == "Recuperação com evidência"

    duplicated = api_client.post(
        "/api/v1/custom-searches",
        json={"copy_from_theme": "tema.credito_contratos_rurais"},
    )
    assert duplicated.status_code == 201, duplicated.text
    assert duplicated.json()["criteria"]["subjects"] == [
        {"code": 4964, "name": "Cédula de Crédito Rural"},
        {"code": 4976, "name": "Nota de Crédito Rural"},
        {"code": 10501, "name": "Crédito Rural"},
    ]

    for invalid_codes in ([108], [4993], [4968], [999999]):
        invalid = api_client.post(
            "/api/v1/custom-searches",
            json={"name": "Inválida", "subject_codes": invalid_codes},
        )
        assert invalid.status_code == 422
    for invalid_classes in ([108], [10501], [999999]):
        invalid = api_client.post(
            "/api/v1/custom-searches",
            json={"name": "Inválida", "class_codes": invalid_classes},
        )
        assert invalid.status_code == 422

    changed = api_client.patch(
        f"/api/v1/custom-searches/{search_id}",
        json={"name": "Recuperação revisada", "dimension_combination": "AND"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2
    assert changed.json()["criteria"]["dimension_combination"] == "AND"
    versions = api_client.get(f"/api/v1/custom-searches/{search_id}/versions")
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [2, 1]
    assert versions.json()[1]["name"] == "Recuperação com evidência"
    assert versions.json()[1]["criteria"]["dimension_combination"] == "OR"

    archived = api_client.post(f"/api/v1/custom-searches/{search_id}/archive")
    assert archived.status_code == 200
    active_searches = api_client.get("/api/v1/custom-searches").json()
    archived_searches = api_client.get(
        "/api/v1/custom-searches", params={"include_archived": "true"}
    ).json()
    assert all(item["id"] != search_id for item in active_searches)
    assert next(item for item in archived_searches if item["id"] == search_id)["archived_at"]
    assert (
        api_client.patch(
            f"/api/v1/custom-searches/{search_id}", json={"name": "Não permitido"}
        ).status_code
        == 409
    )

    preserved_saved = next(
        item
        for item in api_client.get("/api/v1/saved-searches").json()
        if item["id"] == saved.json()["id"]
    )
    assert preserved_saved["search_definition"]["version"] == 1
    assert preserved_saved["definition_name"] == "Recuperação com evidência"
    run = api_client.post(f"/api/v1/saved-searches/{saved.json()['id']}/run")
    assert run.status_code == 202, run.text
    assert run.json()["member_count"] == 1
    group = api_client.get(f"/api/v1/job-groups/{run.json()['id']}")
    assert group.status_code == 200
    assert group.json()["request_snapshot"]["saved_search_version"] == 1
    with Session(api_app.state.database_engine) as session:
        child_job_id = UUID(group.json()["members"][0]["job_id"])
        stored_job = session.scalar(select(Job).where(Job.id == child_job_id))
        assert stored_job is not None
        assert stored_job.parameters_snapshot["query"]["catalog_snapshot"]["name"] == (
            "Recuperação com evidência"
        )
        assert stored_job.parameters_snapshot["query"]["catalog_snapshot"]["preset_version"] == "1"


@pytest.mark.parametrize(
    "tribunals",
    [["TJGO"], ["TJGO", "TJSP", "TRF1"]],
)
def test_official_theme_creates_one_independent_job_per_selected_tribunal(
    api_client: TestClient,
    tribunals: list[str],
) -> None:
    response = api_client.post(
        "/api/v1/job-groups",
        json={
            "kind": "discovery",
            "criteria": {
                "search_definition": {
                    "kind": "official_theme",
                    "id": "tema.recuperacao_judicial",
                    "version": "1.0.0",
                }
            },
            "tribunals": tribunals,
        },
    )
    assert response.status_code == 202, response.text
    group = response.json()
    assert group["member_count"] == len(tribunals)
    assert [member["tribunal"] for member in group["members"]] == tribunals
    assert group["request_snapshot"]["criteria"]["search_definition"]["id"] == (
        "tema.recuperacao_judicial"
    )


def test_legacy_recovery_strategies_deduplicate_cnj_and_keep_discovery_reasons(
    api_app: FastAPI,
    api_client: TestClient,
) -> None:
    worker = LeasedWorker(
        api_app.state.jobs,
        worker_id="api-spec027-recovery-worker",
        handlers={"discovery": build_collection_job_handler(api_app.state.settings)},
        heartbeat_interval=1,
    )
    discovery_requests = [
        {"kind": "discovery", "criteria": {"preset_id": "recuperacao_judicial.por_classe"}},
        {"kind": "discovery", "criteria": {"preset_id": "recuperacao_judicial.por_assunto"}},
        {
            "kind": "discovery",
            "criteria": {
                "search_definition": {
                    "kind": "official_theme",
                    "id": "tema.recuperacao_judicial",
                    "version": "1.0.0",
                }
            },
        },
    ]
    for request_body in discovery_requests:
        created = api_client.post(
            "/api/v1/jobs",
            json=request_body,
        )
        assert created.status_code == 202, created.text
        assert worker.run_once()

    number = "0000001-00.2026.8.09.0001"
    processes = api_client.get("/api/v1/processes", params={"process_number": number})
    assert processes.status_code == 200
    assert processes.json()["total"] == 1
    process = processes.json()["items"][0]
    assert process["numero_cnj"] == "00000010020268090001"
    assert process["representation_count"] == 3

    representations = api_client.get(
        f"/api/v1/processes/{process['id']}/representations", params={"page_size": 10}
    )
    assert representations.status_code == 200
    reasons = [
        reason
        for representation in representations.json()["items"]
        for reason in representation["discovery_reasons"]
    ]
    assert any("Classe 108 — " in reason for reason in reasons)
    assert any("Assunto 4993 — " in reason for reason in reasons)
    assert any(
        "Classe 129 — Recuperação Judicial + Assunto 13277 — Recuperação Judicial" in reason
        for reason in reasons
    )
    theme_options = api_client.get(
        "/api/v1/process-filter-options",
        params={"field": "preset_id", "q": "Recuperação judicial"},
    )
    assert theme_options.status_code == 200
    assert {
        "value": "tema.recuperacao_judicial",
        "label": "Recuperação judicial",
        "detail": None,
        "process_count": 1,
    } in theme_options.json()["items"]


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
    assert completed["coverage"]["budget_limit"] is None
    assert completed["coverage"]["continuations"] == 1


def test_continue_accepts_a_new_cumulative_budget_over_http(
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
        worker_id="api-continue-budget-worker",
        handlers={"discovery": build_collection_job_handler(api_app.state.settings)},
        heartbeat_interval=1,
    )
    assert worker.run_once()

    continued = api_client.post(f"/api/v1/jobs/{job_id}/continue", json={"hit_budget": 100})

    assert continued.status_code == 202
    assert continued.json()["status"] == "queued"
    assert api_app.state.jobs.inspect(job_id).events[-1].details["hit_budget"] == 100


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
        legacy_items = presets.json()["items"]
        assert {item["id"] for item in legacy_items} >= {
            "rural.credito_contratos",
            "execucoes.amplas",
            "recuperacao_judicial.por_classe",
            "recuperacao_judicial.por_assunto",
        }

        search_types = client.get("/api/v1/search-types")
        assert search_types.status_code == 200
        themes = {item["id"]: item for item in search_types.json()["items"]}
        assert set(themes) == {
            "tema.credito_contratos_rurais",
            "tema.recuperacao_judicial",
            "tema.execucoes_relacionadas_ao_agro",
        }
        assert themes["tema.credito_contratos_rurais"]["availability"]["enabled"]
        assert themes["tema.recuperacao_judicial"]["availability"]["enabled"]
        assert not themes["tema.execucoes_relacionadas_ao_agro"]["availability"]["enabled"]

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
