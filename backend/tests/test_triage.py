"""PostgreSQL and HTTP acceptance tests for SPEC-013 human triage."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from agrojud.api.app import create_app
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import make_alembic_config, upgrade_database
from agrojud.db.models import Process, ProcessTriage, ProcessTriageHistory
from agrojud.services.collection import build_collection_job_handler
from agrojud.services.job_worker import LeasedWorker
from agrojud.services.triage import update_triage

DEFAULT_JOB_REQUEST = {
    "kind": "discovery",
    "criteria": {"preset_id": "rural.credito_contratos"},
}


@pytest.fixture
def triage_runtime(scratch_database_url: URL) -> Iterator[tuple[FastAPI, TestClient]]:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    with engine.begin() as connection:
        upgrade_database(connection)
    settings = Settings(
        environment="demo",
        database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    app = create_app(settings, engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client
    engine.dispose()


def _create_process(app: FastAPI, numero_cnj: str | None = None) -> UUID:
    with app.state.session_factory() as session, session.begin():
        process = Process(numero_cnj=numero_cnj or f"{uuid4().int % 10**20:020d}")
        session.add(process)
        session.flush()
        return process.id


def _patch(client: TestClient, process_id: UUID, **body: object):
    return client.patch(f"/api/v1/processes/{process_id}/triage", json=body)


def _counts(app: FastAPI) -> tuple[int, int]:
    with app.state.session_factory() as session:
        return (
            session.scalar(select(text("count(*)")).select_from(ProcessTriage)) or 0,
            session.scalar(select(text("count(*)")).select_from(ProcessTriageHistory)) or 0,
        )


def test_triage_migration_upgrades_previous_revision_without_losing_processes(
    scratch_database_url: URL,
) -> None:
    engine = make_engine(scratch_database_url.render_as_string(hide_password=False))
    try:
        with engine.begin() as connection:
            command.upgrade(make_alembic_config(connection), "20261009_0005")

        with Session(engine) as session, session.begin():
            process = Process(numero_cnj="00000010020248090001")
            session.add(process)
            session.flush()
            process_id = process.id

        with engine.begin() as connection:
            upgrade_database(connection)

        with Session(engine) as session:
            assert session.get(Process, process_id) is not None
        assert {"process_triage", "process_triage_history"}.issubset(
            inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


def test_defaults_are_read_only_and_human_changes_are_reversible_with_history(
    triage_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = triage_runtime
    process_id = _create_process(app)

    detail = client.get(f"/api/v1/processes/{process_id}")
    listed = client.get("/api/v1/processes", params={"decision": "pending"})
    history = client.get(f"/api/v1/processes/{process_id}/triage-history")
    assert detail.status_code == listed.status_code == history.status_code == 200
    assert detail.json()["triage"] == {
        "decision": "pending",
        "rural_link": "unconfirmed",
        "note": "",
        "version": 0,
        "updated_at": None,
    }
    assert listed.json()["total"] == 1
    assert history.json()["items"] == []
    assert _counts(app) == (0, 0)

    relevant = _patch(client, process_id, expected_version=0, decision="relevant")
    assert relevant.status_code == 200
    assert relevant.json()["decision"] == "relevant"
    assert relevant.json()["rural_link"] == "unconfirmed"
    assert relevant.json()["version"] == 1

    confirmed = _patch(
        client,
        process_id,
        expected_version=1,
        rural_link="confirmed",
        note="  Revisado com evidência documental.  ",
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["note"] == "Revisado com evidência documental."
    assert confirmed.json()["version"] == 2

    reversed_link = _patch(
        client,
        process_id,
        expected_version=2,
        rural_link="unconfirmed",
        note="",
    )
    assert reversed_link.status_code == 200
    assert reversed_link.json()["rural_link"] == "unconfirmed"
    assert reversed_link.json()["note"] == ""
    discarded = _patch(client, process_id, expected_version=3, decision="discarded")
    restored = _patch(client, process_id, expected_version=4, decision="pending")
    assert discarded.status_code == restored.status_code == 200
    assert restored.json()["version"] == 5

    no_op = _patch(client, process_id, expected_version=5, decision="pending")
    assert no_op.status_code == 200
    assert no_op.json()["version"] == 5
    history = client.get(f"/api/v1/processes/{process_id}/triage-history")
    body = history.json()
    assert body["total"] == 5
    assert [entry["version"] for entry in body["items"]] == [5, 4, 3, 2, 1]
    first = body["items"][-1]
    assert first["origin"] == "manual"
    assert first["previous_state"]["version"] == 0
    assert first["previous_state"]["decision"] == "pending"
    assert first["new_state"]["decision"] == "relevant"
    assert body["items"][3]["new_state"]["note"] == "Revisado com evidência documental."
    first_page = client.get(
        f"/api/v1/processes/{process_id}/triage-history",
        params={"page": 1, "page_size": 2},
    ).json()
    second_page = client.get(
        f"/api/v1/processes/{process_id}/triage-history",
        params={"page": 2, "page_size": 2},
    ).json()
    assert first_page["total"] == second_page["total"] == 5
    assert [entry["version"] for entry in first_page["items"]] == [5, 4]
    assert [entry["version"] for entry in second_page["items"]] == [3, 2]
    assert _counts(app) == (1, 5)


def test_confirmation_requires_a_trimmed_note_and_patch_rejects_invalid_payloads(
    triage_runtime: tuple[FastAPI, TestClient],
) -> None:
    _app, client = triage_runtime
    process_id = _create_process(_app)

    blank = _patch(
        client,
        process_id,
        expected_version=0,
        decision="relevant",
        rural_link="confirmed",
        note=" \n  ",
    )
    too_long = _patch(client, process_id, expected_version=0, note="x" * 5001)
    null_value = _patch(client, process_id, expected_version=0, note=None)
    assert blank.status_code == too_long.status_code == null_value.status_code == 422
    assert _counts(_app) == (0, 0)

    accepted = _patch(
        client, process_id, expected_version=0, rural_link="confirmed", note="x" * 5000
    )
    assert accepted.status_code == 200
    assert len(accepted.json()["note"]) == 5000


def test_stale_version_conflicts_without_overwriting_the_latest_state(
    triage_runtime: tuple[FastAPI, TestClient],
) -> None:
    _app, client = triage_runtime
    process_id = _create_process(_app)

    first_tab = _patch(
        client, process_id, expected_version=0, decision="relevant", note="primeira aba"
    )
    stale_tab = _patch(
        client, process_id, expected_version=0, decision="discarded", note="rascunho"
    )
    assert first_tab.status_code == 200
    assert stale_tab.status_code == 409
    assert stale_tab.json()["error"]["code"] == "conflict"
    assert stale_tab.json()["error"]["request_id"] == stale_tab.headers["x-request-id"]
    detail = client.get(f"/api/v1/processes/{process_id}")
    assert detail.json()["triage"]["decision"] == "relevant"
    assert detail.json()["triage"]["note"] == "primeira aba"
    assert detail.json()["triage"]["version"] == 1
    assert client.get(f"/api/v1/processes/{process_id}/triage-history").json()["total"] == 1


def test_failed_history_insert_rolls_back_the_state_update(
    triage_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, _client = triage_runtime
    process_id = _create_process(app)

    def reject_history(session: Session, _flush_context: object, _instances: object) -> None:
        if any(isinstance(item, ProcessTriageHistory) for item in session.new):
            raise RuntimeError("simulated history insert failure")

    event.listen(Session, "before_flush", reject_history)
    try:
        with app.state.session_factory() as session:
            with pytest.raises(RuntimeError, match="simulated history insert failure"):
                update_triage(
                    session,
                    process_id,
                    expected_version=0,
                    changes={"decision": "relevant"},
                )
    finally:
        event.remove(Session, "before_flush", reject_history)

    assert _counts(app) == (0, 0)


def test_history_table_rejects_update_and_delete(
    triage_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = triage_runtime
    process_id = _create_process(app)
    changed = _patch(client, process_id, expected_version=0, decision="relevant")
    assert changed.status_code == 200

    with pytest.raises(DBAPIError):
        with app.state.database_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM process_triage_history WHERE process_id = :process_id"),
                {"process_id": process_id},
            )
    with pytest.raises(DBAPIError):
        with app.state.database_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE process_triage_history\n"
                    "SET origin = 'manual'\n"
                    "WHERE process_id = :process_id"
                ),
                {"process_id": process_id},
            )
    assert _counts(app) == (1, 1)


def test_default_filters_include_unreviewed_processes_and_filter_both_dimensions(
    triage_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = triage_runtime
    relevant_id = _create_process(app)
    discarded_id = _create_process(app)
    pending_id = _create_process(app)
    assert _patch(client, relevant_id, expected_version=0, decision="relevant").status_code == 200
    assert (
        _patch(
            client,
            relevant_id,
            expected_version=1,
            rural_link="confirmed",
            note="Vínculo verificado.",
        ).status_code
        == 200
    )
    assert _patch(client, discarded_id, expected_version=0, decision="discarded").status_code == 200

    pending = client.get("/api/v1/processes", params={"decision": "pending"}).json()
    unconfirmed = client.get("/api/v1/processes", params={"rural_link": "unconfirmed"}).json()
    confirmed = client.get(
        "/api/v1/processes", params={"decision": "relevant", "rural_link": "confirmed"}
    ).json()
    assert pending["total"] == 1
    assert pending["items"][0]["id"] == str(pending_id)
    assert unconfirmed["total"] == 2
    assert {item["id"] for item in unconfirmed["items"]} == {
        str(discarded_id),
        str(pending_id),
    }
    assert confirmed["total"] == 1
    assert confirmed["items"][0]["id"] == str(relevant_id)
    assert client.get("/api/v1/processes", params={"decision": "invalid"}).status_code == 422


def test_collection_replay_does_not_change_human_triage(
    triage_runtime: tuple[FastAPI, TestClient],
) -> None:
    app, client = triage_runtime
    first_job = client.post("/api/v1/jobs", json=DEFAULT_JOB_REQUEST)
    assert first_job.status_code == 202
    worker = LeasedWorker(
        app.state.jobs,
        worker_id="triage-acceptance-worker",
        handlers={
            "discovery": build_collection_job_handler(app.state.settings),
            "refresh_number": build_collection_job_handler(app.state.settings),
        },
        heartbeat_interval=1,
    )
    assert worker.run_once()

    process = client.get("/api/v1/processes", params={"decision": "pending"}).json()["items"][0]
    process_id = UUID(process["id"])
    updated = _patch(
        client,
        process_id,
        expected_version=0,
        decision="relevant",
        note="Manter para revisão jurídica.",
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 1

    second_job = client.post("/api/v1/jobs", json=DEFAULT_JOB_REQUEST)
    assert second_job.status_code == 202
    assert second_job.json()["reused"] is False
    assert worker.run_once()
    detail = client.get(f"/api/v1/processes/{process_id}")
    history = client.get(f"/api/v1/processes/{process_id}/triage-history")
    assert detail.json()["triage"]["decision"] == "relevant"
    assert detail.json()["triage"]["note"] == "Manter para revisão jurídica."
    assert detail.json()["triage"]["version"] == 1
    assert history.json()["total"] == 1
