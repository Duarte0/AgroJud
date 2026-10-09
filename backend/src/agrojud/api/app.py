"""Minimal API with independent liveness and database/migration readiness checks."""

from collections.abc import Mapping
from typing import Literal

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from agrojud.config import Settings, get_settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import make_alembic_config

CheckState = Literal["ok", "pending", "unavailable", "unknown"]


class LiveResponse(BaseModel):
    status: Literal["live"] = "live"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: Mapping[str, CheckState]


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Create the health API, accepting a database engine for isolated tests."""

    app = FastAPI(title="AgroJud Radar API", version="0.1.0")
    runtime_settings = settings or get_settings()
    app.state.database_engine = engine or make_engine(runtime_settings.effective_database_url)

    @app.get("/api/v1/health/live", response_model=LiveResponse, tags=["health"])
    def live() -> LiveResponse:
        return LiveResponse()

    @app.get(
        "/api/v1/health/ready",
        response_model=ReadyResponse,
        responses={503: {"description": "Database or Alembic revision is not ready."}},
        tags=["health"],
    )
    def ready(request: Request) -> ReadyResponse | JSONResponse:
        database_engine: Engine = request.app.state.database_engine
        try:
            with database_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                current_heads = set(MigrationContext.configure(connection).get_current_heads())
        except SQLAlchemyError:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "checks": {"database": "unavailable", "migrations": "unknown"},
                },
            )

        expected_heads = set(ScriptDirectory.from_config(make_alembic_config()).get_heads())
        if not expected_heads:
            raise RuntimeError("Alembic has no migration head configured.")

        migrations_state: CheckState = "ok" if current_heads == expected_heads else "pending"
        checks: dict[str, CheckState] = {"database": "ok", "migrations": migrations_state}
        status: Literal["ready", "not_ready"] = "ready" if migrations_state == "ok" else "not_ready"
        response = ReadyResponse(status=status, checks=checks)
        if status == "not_ready":
            return JSONResponse(status_code=503, content=response.model_dump())
        return response

    return app


app = create_app()
