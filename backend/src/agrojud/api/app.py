"""Operational API with independent liveness and database/migration readiness."""

from collections.abc import Mapping
from datetime import timedelta
from typing import Literal
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse

from agrojud.api.errors import (
    error_payload,
    http_exception_response,
    validation_error_response,
)
from agrojud.api.jobs import SourceCapabilityUnavailable
from agrojud.api.jobs import router as jobs_router
from agrojud.api.metadata import router as metadata_router
from agrojud.api.processes import router as processes_router
from agrojud.config import Settings, get_settings
from agrojud.db.engine import make_engine
from agrojud.db.migrations_runner import make_alembic_config
from agrojud.services.jobs import (
    DatabaseUnavailableError,
    InvalidJobTransitionError,
    JobNotFoundError,
    JobService,
)
from agrojud.sources.contracts import SourceError, SourceErrorCode

CheckState = Literal["ok", "pending", "unavailable", "unknown"]


class LiveResponse(BaseModel):
    status: Literal["live"] = "live"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: Mapping[str, CheckState]


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Create the API with injectable settings and engine for isolated tests."""

    app = FastAPI(
        title="AgroJud Radar API",
        version="0.1.0",
        description="API local de jobs, processos, representações, movimentos e presets.",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        redoc_url=None,
    )
    runtime_settings = settings or get_settings()
    database_engine = engine or make_engine(runtime_settings.effective_database_url)
    app.state.settings = runtime_settings
    app.state.database_engine = database_engine
    app.state.session_factory = sessionmaker(database_engine, expire_on_commit=False)
    app.state.jobs = JobService(
        app.state.session_factory,
        lease_duration=timedelta(seconds=runtime_settings.job_lease_seconds),
    )

    @app.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> StarletteResponse:
        request_id = str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        return validation_error_response(request, error)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(
        request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        return http_exception_response(request, error)

    @app.exception_handler(JobNotFoundError)
    async def missing_job(request: Request, _error: JobNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=error_payload(
                request, code="not_found", message="O job solicitado não existe."
            ),
        )

    @app.exception_handler(InvalidJobTransitionError)
    async def invalid_transition(
        request: Request,
        error: InvalidJobTransitionError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=error_payload(request, code="invalid_state", message=str(error)),
        )

    @app.exception_handler(SourceCapabilityUnavailable)
    async def unavailable_source(
        request: Request,
        error: SourceCapabilityUnavailable,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=error_payload(request, code="feature_unavailable", message=str(error)),
        )

    @app.exception_handler(SourceError)
    async def source_error(request: Request, error: SourceError) -> JSONResponse:
        status = 422 if error.code is SourceErrorCode.VALIDATION else 409
        code = "invalid_input" if status == 422 else "feature_unavailable"
        details: dict[str, str] = {}
        if error.field_path is not None:
            details["field_path"] = error.field_path
        if error.validation_code is not None:
            details["validation_code"] = error.validation_code
        return JSONResponse(
            status_code=status,
            content=error_payload(
                request,
                code=code,
                message=error.message,
                details=details or None,
            ),
        )

    @app.exception_handler(DatabaseUnavailableError)
    async def unavailable_database(
        request: Request,
        _error: DatabaseUnavailableError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=error_payload(
                request,
                code="database_unavailable",
                message="O banco de dados está temporariamente indisponível.",
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, _error: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=error_payload(
                request,
                code="database_unavailable",
                message="O banco de dados está temporariamente indisponível.",
            ),
        )

    @app.exception_handler(Exception)
    async def internal_error(request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_payload(
                request,
                code="internal_error",
                message="A solicitação não pôde ser concluída.",
            ),
        )

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

    app.include_router(jobs_router)
    app.include_router(processes_router)
    app.include_router(metadata_router)

    return app


app = create_app()
