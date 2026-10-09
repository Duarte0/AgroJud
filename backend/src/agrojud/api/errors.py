"""Sanitized, uniform HTTP error responses."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agrojud.api.schemas import ErrorBody, ErrorDetail, ErrorResponse

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Recurso não encontrado."},
    409: {"model": ErrorResponse, "description": "Conflito de estado ou capacidade indisponível."},
    422: {"model": ErrorResponse, "description": "Entrada inválida."},
    503: {"model": ErrorResponse, "description": "Banco de dados indisponível."},
}


def error_payload(
    request: Request,
    *,
    code: str,
    message: str,
    details: list[ErrorDetail] | dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_id = getattr(request.state, "request_id", "unknown")
    response = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details,
            request_id=request_id,
        )
    )
    return response.model_dump(mode="json")


def http_exception_response(request: Request, error: StarletteHTTPException) -> JSONResponse:
    status_codes = {
        404: ("not_found", "O recurso solicitado não existe."),
        405: ("method_not_allowed", "O método não está disponível para este recurso."),
        409: ("conflict", "A operação conflita com o estado persistido."),
        422: ("invalid_input", "A entrada informada é inválida."),
    }
    code, message = status_codes.get(
        error.status_code, ("http_error", "A solicitação não pôde ser concluída.")
    )
    return JSONResponse(
        status_code=error.status_code,
        content=error_payload(request, code=code, message=message),
        headers=error.headers,
    )


def validation_error_response(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    details = [
        ErrorDetail(
            location=[part if isinstance(part, (str, int)) else str(part) for part in item["loc"]],
            type=item["type"],
            message=item["msg"],
        )
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=error_payload(
            request,
            code="invalid_input",
            message="A entrada informada é inválida.",
            details=details,
        ),
    )
