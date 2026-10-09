"""Health contract tests against isolated PostgreSQL databases."""

import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import inspect
from sqlalchemy.engine import URL

from agrojud.api.app import create_app
from agrojud.config import Settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import upgrade_database


def get(app: FastAPI, path: str) -> Response:
    async def request() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.get(path)

    return asyncio.run(request())


def test_live_does_not_depend_on_database(scratch_database_url: URL) -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_demo",
        test_database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    engine = make_engine("postgresql+psycopg://agrojud:secret@127.0.0.1:1/agrojud_offline_test")

    try:
        response = get(create_app(settings, engine), "/api/v1/health/live")
    finally:
        engine.dispose()

    assert response.status_code == 200
    assert response.json() == {"status": "live"}


def test_ready_reports_pending_migration_without_writing(scratch_database_url: URL) -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_demo",
        test_database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    engine = make_engine(settings.effective_database_url)
    try:
        response = get(create_app(settings, engine), "/api/v1/health/ready")

        assert response.status_code == 503
        assert response.json() == {
            "status": "not_ready",
            "checks": {"database": "ok", "migrations": "pending"},
        }
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_ready_is_200_at_alembic_head_with_domain_tables(scratch_database_url: URL) -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_demo",
        test_database_url=scratch_database_url.render_as_string(hide_password=False),
    )
    engine = make_engine(settings.effective_database_url)
    try:
        with engine.connect() as connection:
            upgrade_database(connection)

        response = get(create_app(settings, engine), "/api/v1/health/ready")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "checks": {"database": "ok", "migrations": "ok"},
        }
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "collection_observations",
            "collection_results",
            "collections",
            "job_attempts",
            "job_checkpoints",
            "job_events",
            "jobs",
            "movement_occurrences",
            "movement_snapshot_occurrences",
            "movement_snapshots",
            "processes",
            "quarantine_rejections",
            "quarantine_resolutions",
            "representation_subjects",
            "representation_versions",
            "representations",
            "source_rate_limits",
        }
    finally:
        engine.dispose()


def test_ready_reports_unavailable_database_without_leaking_url(scratch_database_url: URL) -> None:
    settings = Settings(
        environment="test",
        database_url="postgresql+psycopg://agrojud:secret@localhost:5432/agrojud_demo",
        test_database_url="postgresql+psycopg://agrojud:topsecret@127.0.0.1:1/agrojud_offline_test",
    )

    response = get(create_app(settings), "/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "unavailable", "migrations": "unknown"},
    }
    assert "topsecret" not in response.text
    assert str(scratch_database_url) not in response.text
