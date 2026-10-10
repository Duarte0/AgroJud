"""Download endpoints for complete exports of locally persisted data."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.process_filters import (
    ProcessFilters,
    SignalEnvironment,
    process_filters_dependency,
)
from agrojud.services.exports import (
    ProcessCsvExport,
    create_process_csv_export,
)

router = APIRouter(prefix="/api/v1/exports", tags=["exports"])
_ERROR_SCHEMA = {"$ref": "#/components/schemas/ErrorResponse"}
_JSON_ERROR_RESPONSES = {
    status: {
        "description": response["description"],
        "content": {"application/json": {"schema": _ERROR_SCHEMA}},
    }
    for status, response in ERROR_RESPONSES.items()
}
_JSON_ERROR_RESPONSES[422] = {
    "description": "Entrada inválida ou limite síncrono de exportação excedido.",
    "content": {"application/json": {"schema": _ERROR_SCHEMA}},
}


class TemporaryCsvFileResponse(Response):
    """Send a prebuilt file and remove it on completion, disconnect, or cancellation."""

    media_type = "text/csv"

    def __init__(self, export: ProcessCsvExport) -> None:
        self.path: Path = export.path
        headers = {"Content-Disposition": f'attachment; filename="{export.filename}"'}
        super().__init__(content=b"", media_type=self.media_type, headers=headers)
        self.raw_headers = [
            (name, value) for name, value in self.raw_headers if name.lower() != b"content-length"
        ]

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        try:
            file_size = await anyio.to_thread.run_sync(lambda: self.path.stat().st_size)
            headers = [
                (name, value)
                for name, value in self.raw_headers
                if name.lower() != b"content-length"
            ]
            headers.append((b"content-length", str(file_size).encode("ascii")))
            await send(
                {
                    "type": "http.response.start",
                    "status": self.status_code,
                    "headers": headers,
                }
            )
            async with await anyio.open_file(self.path, "rb") as csv_file:
                while chunk := await csv_file.read(64 * 1024):
                    await send(
                        {
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": True,
                        }
                    )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            self.path.unlink(missing_ok=True)


@router.get(
    "/processes.csv",
    status_code=200,
    response_class=TemporaryCsvFileResponse,
    responses=_JSON_ERROR_RESPONSES,
    summary="Exporta todos os processos do recorte em CSV",
    description=(
        "Aplica os mesmos filtros da listagem, em uma leitura consistente, sem paginação. "
        "A coluna numero_cnj deve ser importada como texto em planilhas."
    ),
)
def export_processes_csv(
    request: Request,
    filters: Annotated[ProcessFilters, Depends(process_filters_dependency)],
) -> TemporaryCsvFileResponse:
    configured_environment = request.app.state.settings.environment
    environment: SignalEnvironment = "real" if configured_environment == "real" else "demo"
    export = create_process_csv_export(
        request.app.state.session_factory,
        filters,
        environment=environment,
        process_limit=request.app.state.settings.export_process_limit,
    )
    return TemporaryCsvFileResponse(export)
