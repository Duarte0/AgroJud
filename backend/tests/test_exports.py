"""PostgreSQL-backed CSV export acceptance and safety coverage for SPEC-019."""

from __future__ import annotations

import asyncio
import csv
import io
from collections.abc import Iterator
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from agrojud.api.app import create_app
from agrojud.api.exports import TemporaryCsvFileResponse
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database
from agrojud.db.models import (
    Base,
    Collection,
    CollectionObservation,
    CollectionResult,
    Process,
    ProcessTriage,
    ProcessWatchlistEntry,
    Representation,
    RepresentationSubject,
    RepresentationVersion,
)
from agrojud.services import exports
from agrojud.services.exports import (
    CSV_HEADERS,
    ProcessCsvExport,
    neutralize_spreadsheet_formula,
)

OBSERVED_AT = datetime(2026, 10, 9, 12, tzinfo=UTC)
CAPTURE_REASON = "Classe e assunto identificados no preset de crédito rural."


@pytest.fixture
def export_runtime(scratch_database_url: URL) -> Iterator[tuple[FastAPI, TestClient]]:
    database_url = scratch_database_url.render_as_string(hide_password=False)
    engine = make_engine(database_url)
    with engine.begin() as connection:
        upgrade_database(connection)
    app = create_app(
        Settings(environment="demo", database_url=database_url, export_process_limit=2),
        engine,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client
    engine.dispose()


@pytest.fixture
def export_seed(export_runtime: tuple[FastAPI, TestClient]) -> dict[str, UUID]:
    app, _client = export_runtime
    ids = {
        key: uuid4()
        for key in (
            "process",
            "other_process",
            "rep_one",
            "rep_two",
            "collection",
            "version",
            "observation",
        )
    }
    first_seen = OBSERVED_AT.replace(hour=10)
    last_seen = OBSERVED_AT.replace(hour=14)
    malicious_class = '  =HYPERLINK("http://example.invalid";"abrir")'
    malicious_subject = "\t+SUM(1;1)"
    malicious_unit = "\r=1+1"

    with app.state.session_factory() as session, session.begin():
        process = Process(id=ids["process"], numero_cnj="00000010020268090001")
        other_process = Process(id=ids["other_process"], numero_cnj="00000020020268090001")
        session.add_all([process, other_process])
        session.flush()

        first_representation = Representation(
            id=ids["rep_one"],
            process_id=process.id,
            source="synthetic",
            tribunal="TJGO",
            source_id="export-seed-one",
            class_code="1",
            class_name=malicious_class,
            grau="1º Grau",
            court_unit_code="10",
            court_unit_name=malicious_unit,
            last_observed_at=OBSERVED_AT,
        )
        second_representation = Representation(
            id=ids["rep_two"],
            process_id=process.id,
            source="synthetic",
            tribunal="TJMG",
            source_id="export-seed-two",
            class_code="2",
            class_name="Execução rural",
            grau="2º Grau",
            court_unit_code="20",
            court_unit_name="Órgão julgador",
            last_observed_at=last_seen,
        )
        session.add_all([first_representation, second_representation])
        session.add(
            Collection(
                id=ids["collection"],
                mode="demo",
                resolved_criteria={
                    "query": {"catalog_snapshot": {"capture_explanation": CAPTURE_REASON}}
                },
                criteria_sha256="a" * 64,
            )
        )
        session.flush()

        version = RepresentationVersion(
            id=ids["version"],
            representation_id=first_representation.id,
            payload_sha256=sha256(b"export-seed").hexdigest(),
            raw_payload={},
            normalizer_version="cover-normalizer-v1",
            first_observed_at=first_seen,
        )
        session.add(version)
        session.flush()
        first_representation.latest_version_id = version.id
        session.flush()

        observation = CollectionObservation(
            id=ids["observation"],
            collection_id=ids["collection"],
            representation_id=first_representation.id,
            page_key="0",
            hit_ordinal=1,
            version_id=version.id,
            observed_at=first_seen,
            capture_outcome="new",
            source_filed_at_regressed=False,
        )
        session.add(observation)
        session.flush()
        session.add(
            CollectionResult(
                collection_id=ids["collection"],
                representation_id=first_representation.id,
                first_observation_id=observation.id,
                included_at=first_seen,
                capture_outcome="new",
            )
        )
        session.add_all(
            [
                RepresentationSubject(
                    representation_id=first_representation.id,
                    ordinal=1,
                    subject_code="4968",
                    subject_name=malicious_subject,
                ),
                RepresentationSubject(
                    representation_id=first_representation.id,
                    ordinal=2,
                    subject_code="4968",
                    subject_name=malicious_subject,
                ),
                RepresentationSubject(
                    representation_id=second_representation.id,
                    ordinal=1,
                    subject_code="4968",
                    subject_name=malicious_subject,
                ),
            ]
        )
        session.add(
            ProcessTriage(
                process_id=process.id,
                decision="relevant",
                rural_link="confirmed",
                note="Vínculo confirmado na revisão local.",
                version=1,
                updated_at=OBSERVED_AT,
            )
        )
        session.add(
            ProcessWatchlistEntry(
                process_id=process.id,
                active=True,
                included_at=OBSERVED_AT,
                next_run_at=last_seen,
            )
        )
    return ids


def test_csv_matches_filtered_process_set_and_preserves_multirepresentation_values(
    export_runtime: tuple[FastAPI, TestClient],
    export_seed: dict[str, UUID],
) -> None:
    app, client = export_runtime
    filters = {"subject_code": "4968", "page": 99, "page_size": 1}
    before_counts = _domain_counts(app)
    list_page = client.get("/api/v1/processes", params={**filters, "page": 1, "page_size": 1})
    response = client.get("/api/v1/exports/processes.csv", params=filters)

    assert list_page.status_code == 200
    assert list_page.json()["total"] == 1
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="agrojud-processos-demo.csv"'
    )
    assert response.content.startswith(b"\xef\xbb\xbf")

    rows = list(
        csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter=";")
    )
    assert tuple(rows[0]) == CSV_HEADERS
    assert len(rows) == 2
    row = dict(zip(rows[0], rows[1], strict=True))
    assert row["numero_cnj"] == "00000010020268090001"
    assert row["tribunal"] == "TJGO; TJMG"
    assert row["classes"].startswith("'  =HYPERLINK(")
    assert row["classes"].endswith("; Execução rural (2)")
    assert row["graus"] == "1º Grau; 2º Grau"
    assert row["orgaos"].startswith("'\r=1+1 (10)")
    assert row["assuntos"] == "'\t+SUM(1;1) (4968)"
    assert row["triagem"] == "Relevante"
    assert row["vinculo_rural"] == "Confirmado"
    assert row["motivos_captura"] == CAPTURE_REASON
    assert row["acompanhado"] == "Sim"
    assert row["primeira_observacao"] == "2026-10-09T10:00:00Z"
    assert row["ultima_observacao"] == "2026-10-09T14:00:00Z"
    assert row["data_source"] == "demo"
    assert row["exportado_em"].endswith("Z")
    assert _domain_counts(app) == before_counts


def test_empty_result_contains_only_headers_and_openapi_excludes_pagination(
    export_runtime: tuple[FastAPI, TestClient],
) -> None:
    _app, client = export_runtime
    empty = client.get(
        "/api/v1/exports/processes.csv", params={"process_number": "99999990020268090001"}
    )
    assert empty.status_code == 200
    rows = list(
        csv.reader(io.StringIO(empty.content.decode("utf-8-sig"), newline=""), delimiter=";")
    )
    assert rows == [list(CSV_HEADERS)]

    openapi = client.get("/api/v1/openapi.json").json()
    paths = openapi["paths"]
    assert paths["/api/v1/exports/processes.csv"]["get"]["responses"]["200"]["content"].keys() == {
        "text/csv"
    }
    assert paths["/api/v1/exports/processes.csv"]["get"]["responses"]["422"]["content"] == {
        "application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}
    }
    export_parameters = {
        parameter["name"]
        for parameter in paths["/api/v1/exports/processes.csv"]["get"]["parameters"]
    }
    list_parameters = {
        parameter["name"] for parameter in paths["/api/v1/processes"]["get"]["parameters"]
    }
    assert export_parameters == list_parameters - {"page", "page_size"}


def test_limit_accepts_exact_boundary_and_returns_structured_error_above_it(
    export_runtime: tuple[FastAPI, TestClient],
    export_seed: dict[str, UUID],
) -> None:
    app, client = export_runtime
    exact = client.get("/api/v1/exports/processes.csv")
    assert exact.status_code == 200

    with app.state.session_factory() as session, session.begin():
        session.add(Process(numero_cnj="00000030020268090001"))
    exceeded = client.get("/api/v1/exports/processes.csv")
    assert exceeded.status_code == 422
    assert exceeded.json()["error"]["code"] == "EXPORT_LIMIT_EXCEEDED"
    assert exceeded.json()["error"]["details"] == {"limit": 2}
    assert "Restrinja os filtros" in exceeded.json()["error"]["message"]
    assert "content-disposition" not in exceeded.headers


def test_export_keeps_the_initial_snapshot_when_a_process_is_inserted_mid_generation(
    export_runtime: tuple[FastAPI, TestClient],
    export_seed: dict[str, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client = export_runtime
    original_loader = exports._load_process_values
    inserted = False

    def insert_after_first_batch(session: Session, processes: list[Process]):
        nonlocal inserted
        values = original_loader(session, processes)
        if not inserted:
            with Session(app.state.database_engine) as writer, writer.begin():
                writer.add(Process(numero_cnj="00000030020268090001"))
            inserted = True
        return values

    monkeypatch.setattr(exports, "_load_process_values", insert_after_first_batch)
    response = client.get("/api/v1/exports/processes.csv")

    assert response.status_code == 200
    rows = list(
        csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter=";")
    )
    assert [row[0] for row in rows[1:]] == [
        "00000010020268090001",
        "00000020020268090001",
    ]
    with Session(app.state.database_engine) as session:
        assert session.scalar(select(func.count()).select_from(Process)) == 3


def _domain_counts(app: FastAPI) -> dict[str, int]:
    with Session(app.state.database_engine) as session:
        return {
            table.name: int(session.scalar(select(func.count()).select_from(table)) or 0)
            for table in Base.metadata.sorted_tables
        }


def test_database_failure_is_not_reported_as_an_empty_csv(
    scratch_database_url: URL,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exports.tempfile, "tempdir", str(tmp_path))
    unavailable_engine = make_engine(
        "postgresql+psycopg://agrojud:secret@127.0.0.1:1/agrojud_exports_unavailable_test"
        "?connect_timeout=1"
    )
    app = create_app(
        Settings(
            environment="demo",
            database_url=scratch_database_url.render_as_string(hide_password=False),
        ),
        unavailable_engine,
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/exports/processes.csv")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_unavailable"
        assert "content-disposition" not in response.headers
        assert "text/csv" not in response.headers["content-type"]
        assert not list(tmp_path.iterdir())
    finally:
        unavailable_engine.dispose()


@pytest.mark.parametrize(
    "value",
    ["=1+1", "+SUM(1;1)", "-1+1", "@SUM(1)", "\t=1+1", "\r=1+1", " \u00a0@SUM(1)"],
)
def test_spreadsheet_formula_prefixes_are_neutralized_after_whitespace(value: str) -> None:
    assert neutralize_spreadsheet_formula(value) == f"'{value}"


def test_generation_failure_returns_structured_error_and_removes_temporary_artifact(
    export_runtime: tuple[FastAPI, TestClient],
    export_seed: dict[str, UUID],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app, client = export_runtime
    monkeypatch.setattr(exports.tempfile, "tempdir", str(tmp_path))

    def fail_generation(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated temporary disk failure")

    monkeypatch.setattr(exports, "_write_process_rows", fail_generation)
    response = client.get("/api/v1/exports/processes.csv")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert not list(tmp_path.iterdir())
    assert "text/csv" not in response.headers["content-type"]


def test_temporary_artifact_is_removed_when_response_is_cancelled(tmp_path: Path) -> None:
    path = tmp_path / "export.csv"
    path.write_bytes(b"\xef\xbb\xbfheader\r\nrow\r\n")
    response = TemporaryCsvFileResponse(
        ProcessCsvExport(path=path, filename="agrojud-processos-demo.csv", exported_at=OBSERVED_AT)
    )

    async def exercise_cancellation() -> None:
        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, object]) -> None:
            if message["type"] == "http.response.body":
                raise asyncio.CancelledError

        scope: dict[str, object] = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/exports/processes.csv",
            "headers": [],
            "query_string": b"",
        }
        with pytest.raises(asyncio.CancelledError):
            await response(scope, receive, send)  # type: ignore[arg-type]

    asyncio.run(exercise_cancellation())
    assert not path.exists()
