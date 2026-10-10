"""PostgreSQL acceptance tests for local, explainable signal reprocessing."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import URL

from agrojud.api.app import create_app
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    Process,
    ProcessSignal,
    Representation,
    RepresentationVersion,
    SignalEvaluation,
    SignalRunInput,
)
from agrojud.domain.canonical_json import sha256_json
from agrojud.domain.movement_normalization import NORMALIZER_VERSION
from agrojud.domain.occurrence_identity import NORMALIZER_VERSION as OCCURRENCE_VERSION
from agrojud.domain.signals import (
    SIGNAL_RULES,
    InactiveSignalRuleError,
    evaluate_rule,
    select_signal_rules,
)
from agrojud.services import signal_reprocessing as signal_reprocessing_module
from agrojud.services.job_worker import LeasedWorker
from agrojud.services.movement_ingestion import persist_movement_snapshot
from agrojud.services.signal_reprocessing import build_signal_run_handler


@pytest.fixture
def signal_runtime(scratch_database_url: URL) -> Iterator[tuple[FastAPI, TestClient]]:
    database_url = scratch_database_url.render_as_string(hide_password=False)
    engine = make_engine(database_url)
    with engine.begin() as connection:
        upgrade_database(connection)
    app = create_app(Settings(environment="demo", database_url=database_url), engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client
    engine.dispose()


def _seed_process(
    app: FastAPI,
    codes: tuple[int, ...] = (11382,),
    *,
    process_number: str | None = None,
) -> tuple[UUID, tuple[UUID, ...]]:
    observed_at = datetime(2026, 9, 1, 12, tzinfo=UTC)
    with app.state.session_factory() as session, session.begin():
        process = Process(numero_cnj=process_number or f"{uuid4().int % 10**20:020d}")
        session.add(process)
        session.flush()
        representation_ids: list[UUID] = []
        for index, code in enumerate(codes):
            source_id = f"synthetic-signal-{uuid4().hex}-{index}"
            representation = Representation(
                process_id=process.id,
                source="synthetic",
                tribunal="TJGO",
                source_id=source_id,
                last_observed_at=observed_at,
            )
            session.add(representation)
            session.flush()
            representation_ids.append(representation.id)
            payload = {
                "id": source_id,
                "numeroProcesso": process.numero_cnj,
                "tribunal": "TJGO",
                "movimentos": [
                    {"codigo": code, "nome": "fixture local", "dataHora": observed_at.isoformat()}
                ],
            }
            version = RepresentationVersion(
                id=uuid4(),
                representation_id=representation.id,
                payload_sha256=sha256_json(payload),
                raw_payload=payload,
                normalizer_version=OCCURRENCE_VERSION,
                first_observed_at=observed_at,
            )
            session.add(version)
            session.flush()
            persist_movement_snapshot(
                session,
                representation_id=representation.id,
                version_id=version.id,
                raw_source=payload,
                observed_at=observed_at,
                first_observed_at=observed_at,
                normalizer_version=NORMALIZER_VERSION,
            )
            representation.latest_version_id = version.id
        process_id = process.id
    return process_id, tuple(representation_ids)


def _add_incomplete_latest_version(app: FastAPI, representation_id: UUID) -> None:
    observed_at = datetime(2026, 9, 2, 12, tzinfo=UTC)
    with app.state.session_factory() as session, session.begin():
        representation = session.get(Representation, representation_id)
        assert representation is not None
        payload = {
            "id": representation.source_id,
            "numeroProcesso": "00000000000000000001",
            "tribunal": "TJGO",
            "@timestamp": observed_at.isoformat(),
        }
        version = RepresentationVersion(
            id=uuid4(),
            representation_id=representation.id,
            payload_sha256=sha256_json(payload),
            raw_payload=payload,
            normalizer_version=OCCURRENCE_VERSION,
            first_observed_at=observed_at,
        )
        session.add(version)
        session.flush()
        persist_movement_snapshot(
            session,
            representation_id=representation.id,
            version_id=version.id,
            raw_source=payload,
            observed_at=observed_at,
            first_observed_at=observed_at,
            normalizer_version=NORMALIZER_VERSION,
        )
        representation.latest_version_id = version.id
        representation.last_observed_at = observed_at


def _run_worker(app: FastAPI) -> None:
    handler = build_signal_run_handler(app.state.jobs)
    worker = LeasedWorker(
        app.state.jobs,
        worker_id="signal-spec-test-worker",
        handlers={"reprocess_rules": handler},
        heartbeat_interval=1,
    )
    assert worker.run_once()


def test_rule_engine_is_exact_structured_and_diagnoses_missing_inputs() -> None:
    penhora = next(rule for rule in SIGNAL_RULES if rule.id == "sinal.penhora")
    evidence_id = uuid4()

    positive = evaluate_rule(
        penhora,
        [(evidence_id, {"codigo": "11382", "nome": "texto irrelevante"})],
        input_complete=True,
    )
    negative = evaluate_rule(
        penhora,
        [(evidence_id, {"codigo": 311, "nome": "penhora escrita no nome"})],
        input_complete=True,
    )
    incomplete = evaluate_rule(penhora, [], input_complete=False)
    missing_code = evaluate_rule(
        penhora, [(evidence_id, {"nome": "sem código"})], input_complete=True
    )

    assert positive.outcome == "matched"
    assert positive.matches[0].evidence_id == evidence_id
    assert negative.outcome == "no_match"
    assert incomplete.outcome == "not_evaluated"
    assert missing_code.outcome == "not_evaluated"


def test_repeated_run_is_idempotent_explainable_and_preserves_triage(
    signal_runtime: tuple[FastAPI, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client = signal_runtime
    process_id, _ = _seed_process(app)
    triage = client.patch(
        f"/api/v1/processes/{process_id}/triage",
        json={"expected_version": 0, "decision": "relevant", "note": "revisar com o cliente"},
    )
    assert triage.status_code == 200

    first = client.post("/api/v1/rule-runs", json={"process_ids": [str(process_id)]})
    assert first.status_code == 202
    first_body = first.json()
    assert first_body["process_count"] == 1
    assert first_body["input_count"] == 1
    assert app.state.jobs.inspect(UUID(first_body["job_id"])).collection_id is None

    def reject_http(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("O reprocessamento local tentou chamar HTTP.")

    with monkeypatch.context() as network_guard:
        network_guard.setattr(httpx.Client, "send", reject_http)
        network_guard.setattr(httpx.AsyncClient, "send", reject_http)
        _run_worker(app)

    run = client.get(f"/api/v1/rule-runs/{first_body['job_id']}")
    assert run.status_code == 200
    assert run.json()["status"] == "completed"
    assert run.json()["processed_input_count"] == 1

    signals = client.get(f"/api/v1/processes/{process_id}/signals")
    assert signals.status_code == 200
    body = signals.json()
    assert len(body["items"]) == 1
    signal = body["items"][0]
    assert signal["state"] == "current"
    assert signal["rule_id"] == "sinal.penhora"
    assert signal["rule_version"] == "1.0.0"
    assert signal["environment"] == "demo"
    assert signal["rule_enablement"]["state"] == "synthetic_only"
    assert "não confirma" in signal["explanation"]

    timeline = client.get(
        f"/api/v1/processes/{process_id}/movements",
        params={"occurrence_id": signal["movement_occurrence_id"]},
    )
    assert timeline.status_code == 200
    assert timeline.json()["total"] == 1
    assert timeline.json()["items"][0]["content"]["codigo"] == 11382

    second = client.post("/api/v1/rule-runs", json={"process_ids": [str(process_id)]})
    assert second.status_code == 202
    _run_worker(app)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ProcessSignal)) == 1
        assert session.scalar(select(func.count()).select_from(SignalEvaluation)) == 6

    process = client.get(f"/api/v1/processes/{process_id}")
    assert process.json()["triage"]["decision"] == "relevant"
    assert process.json()["triage"]["note"] == "revisar com o cliente"
    history = client.get(f"/api/v1/processes/{process_id}/triage-history")
    assert history.json()["total"] == 1


def test_incomplete_latest_representation_keeps_old_signal_as_stale(
    signal_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = signal_runtime
    process_id, representation_ids = _seed_process(app)
    created = client.post("/api/v1/rule-runs", json={"process_ids": [str(process_id)]})
    assert created.status_code == 202
    _run_worker(app)

    _add_incomplete_latest_version(app, representation_ids[0])
    reprocessed = client.post("/api/v1/rule-runs", json={"process_ids": [str(process_id)]})
    assert reprocessed.status_code == 202
    _run_worker(app)

    signals = client.get(f"/api/v1/processes/{process_id}/signals").json()
    assert len(signals["items"]) == 1
    assert signals["items"][0]["state"] == "current"
    assert signals["items"][0]["evidence_stale"] is True
    run = signals["latest_run"]
    assert run["status"] == "completed"
    assert run["stale_process_count"] == 1


def test_failed_batch_preserves_previous_publication_and_resumes_from_local_cursor(
    signal_runtime: tuple[FastAPI, TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client = signal_runtime
    process_id, _ = _seed_process(app, (11382, 11382))
    first = client.post("/api/v1/rule-runs", json={"process_ids": [str(process_id)]})
    assert first.status_code == 202
    _run_worker(app)

    original_select = signal_reprocessing_module.select_signal_rules

    def version_two(environment: str, selected_ids: object = None):
        rules = original_select(environment, selected_ids)  # type: ignore[arg-type]
        if selected_ids == ["sinal.penhora"]:
            return (replace(rules[0], version="2.0.0"),)
        return rules

    monkeypatch.setattr(signal_reprocessing_module, "select_signal_rules", version_two)
    monkeypatch.setattr(signal_reprocessing_module, "INPUT_BATCH_SIZE", 1)
    second = client.post(
        "/api/v1/rule-runs",
        json={"process_ids": [str(process_id)], "rule_ids": ["sinal.penhora"]},
    )
    assert second.status_code == 202
    job_id = second.json()["job_id"]

    original_commit = signal_reprocessing_module._commit_next_batch
    call_count = 0

    def fail_after_one_batch(*args: object, **kwargs: object) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("injected interruption between local batches")
        return original_commit(*args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as interruption:
        interruption.setattr(signal_reprocessing_module, "_commit_next_batch", fail_after_one_batch)
        _run_worker(app)

    failed = client.get(f"/api/v1/rule-runs/{job_id}").json()
    assert failed["status"] == "failed"
    assert failed["processed_input_count"] == 1
    assert failed["resumable"] is True
    visible = client.get(f"/api/v1/processes/{process_id}/signals").json()["items"]
    assert len(visible) == 2
    assert all(item["state"] == "current" and item["rule_version"] == "1.0.0" for item in visible)

    resumed = client.post(f"/api/v1/rule-runs/{job_id}/resume")
    assert resumed.status_code == 202
    assert resumed.json()["job_id"] == job_id
    _run_worker(app)

    visible = client.get(f"/api/v1/processes/{process_id}/signals").json()["items"]
    current = [item for item in visible if item["state"] == "current"]
    historical = [item for item in visible if item["state"] == "historical"]
    assert len(current) == len(historical) == 2
    assert {item["rule_version"] for item in current} == {"2.0.0"}
    assert {item["rule_version"] for item in historical} == {"1.0.0"}
    with app.state.session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(SignalRunInput)
                .where(SignalRunInput.job_id == UUID(job_id), SignalRunInput.status == "completed")
            )
            == 2
        )


def test_empty_selection_completes_and_real_rules_remain_gated(
    signal_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = signal_runtime
    created = client.post("/api/v1/rule-runs", json={"process_ids": []})
    assert created.status_code == 202
    assert created.json()["process_count"] == created.json()["input_count"] == 0
    _run_worker(app)
    status = client.get(f"/api/v1/rule-runs/{created.json()['job_id']}")
    assert status.json()["status"] == "completed"
    assert status.json()["processed_input_count"] == 0
    assert status.json()["coverage"]["total_processes"] == 0

    unknown = client.post(
        "/api/v1/rule-runs",
        json={"process_ids": [], "rule_ids": ["sinal.inexistente"]},
    )
    assert unknown.status_code == 422

    process_id, _ = _seed_process(app)
    demo_run = client.post("/api/v1/rule-runs", json={"process_ids": [str(process_id)]})
    assert demo_run.status_code == 202
    _run_worker(app)
    assert len(client.get(f"/api/v1/processes/{process_id}/signals").json()["items"]) == 1

    database_url = app.state.settings.database_url
    real_app = create_app(
        Settings(environment="real", database_url=database_url), app.state.database_engine
    )
    with TestClient(real_app, raise_server_exceptions=False) as real_client:
        gated = real_client.post("/api/v1/rule-runs", json={"process_ids": []})
        hidden_demo_signals = real_client.get(f"/api/v1/processes/{process_id}/signals")
        hidden_demo_run = real_client.get(f"/api/v1/rule-runs/{demo_run.json()['job_id']}")
        hidden_demo_resume = real_client.post(
            f"/api/v1/rule-runs/{demo_run.json()['job_id']}/resume"
        )
    assert gated.status_code == 409
    assert hidden_demo_signals.status_code == 200
    assert hidden_demo_signals.json() == {
        "process_id": str(process_id),
        "items": [],
        "latest_run": None,
    }
    assert hidden_demo_run.status_code == 404
    assert hidden_demo_resume.status_code == 404


def test_real_signal_rules_follow_s5_evidence() -> None:
    enabled = select_signal_rules("real", ["sinal.leilao", "sinal.recuperacao_judicial"])

    assert [rule.id for rule in enabled] == ["sinal.leilao", "sinal.recuperacao_judicial"]
    assert all(rule.enablement["real"].state == "validated" for rule in enabled)
    with pytest.raises(InactiveSignalRuleError, match="11382"):
        select_signal_rules("real", ["sinal.penhora"])
