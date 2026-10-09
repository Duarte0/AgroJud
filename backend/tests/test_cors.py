"""CORS is limited to the single configured local frontend origin."""

import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.engine import Engine

from agrojud.api.app import create_app
from agrojud.config import Settings
from agrojud.db.engine import make_engine

FRONTEND_ORIGIN = "http://127.0.0.1:4173"


def preflight(app: FastAPI, origin: str) -> Response:
    async def request() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.options(
                "/api/v1/jobs",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )

    return asyncio.run(request())


def live(app: FastAPI, origin: str) -> Response:
    async def request() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.get("/api/v1/health/live", headers={"Origin": origin})

    return asyncio.run(request())


def offline_app() -> tuple[FastAPI, Engine]:
    settings = Settings(
        environment="demo",
        database_url="postgresql+psycopg://agrojud:secret@127.0.0.1:1/agrojud_offline",
        frontend_origin=FRONTEND_ORIGIN,
    )
    engine = make_engine(settings.effective_database_url)
    return create_app(settings, engine), engine


def test_configured_origin_receives_cors_headers() -> None:
    app, engine = offline_app()
    try:
        allowed = preflight(app, FRONTEND_ORIGIN)
        simple = live(app, FRONTEND_ORIGIN)
    finally:
        engine.dispose()

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
    assert "POST" in allowed.headers["access-control-allow-methods"]
    assert simple.headers["access-control-allow-origin"] == FRONTEND_ORIGIN
    exposed = simple.headers["access-control-expose-headers"].lower()
    assert "location" in exposed
    assert "x-request-id" in exposed


def test_other_origins_are_not_allowed() -> None:
    app, engine = offline_app()
    try:
        responses = [
            preflight(app, "http://localhost:5173"),
            preflight(app, "http://evil.example:4173"),
            live(app, "http://evil.example:4173"),
        ]
    finally:
        engine.dispose()

    assert responses[0].status_code == 400
    assert responses[1].status_code == 400
    for response in responses:
        assert "access-control-allow-origin" not in response.headers
