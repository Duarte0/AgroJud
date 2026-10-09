"""Single-attempt HTTPX adapter for the public TJGO DataJud endpoint."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from agrojud.config import Settings
from agrojud.sources.contracts import (
    Cursor,
    SourceError,
    SourceErrorCode,
    SourcePage,
    SourceQuery,
    build_datajud_payload,
    build_query_by_case_number,
    parse_source_page,
    validate_fetch_arguments,
)

LOGGER = logging.getLogger(__name__)
DATAJUD_TJGO_ENDPOINT = "https://api-publica.datajud.cnj.jus.br/api_publica_tjgo/_search"


class DataJudSourceAdapter:
    """Calls only the public TJGO endpoint and performs no retries."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if settings.environment == "demo":
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "A fonte DataJud não pode ser usada no ambiente demo.",
            )
        if settings.environment == "test" and transport is None:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O ambiente test exige transporte HTTP simulado para DataJud.",
            )
        if settings.datajud_api_key is None:
            raise SourceError(
                SourceErrorCode.AUTHENTICATION,
                "DATAJUD_API_KEY é obrigatória para selecionar a fonte DataJud.",
            )
        api_key = settings.datajud_api_key.get_secret_value()
        if not api_key.strip():
            raise SourceError(
                SourceErrorCode.AUTHENTICATION,
                "DATAJUD_API_KEY é obrigatória para selecionar a fonte DataJud.",
            )

        self._authorization = f"APIKey {api_key}"
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0),
            follow_redirects=False,
        )

    def fetch_page(
        self,
        query: SourceQuery,
        cursor: Cursor | None,
        page_size: int,
    ) -> SourcePage:
        normalized_cursor = validate_fetch_arguments(query, cursor, page_size)
        payload = build_datajud_payload(query, normalized_cursor, page_size)
        try:
            response = self._client.post(
                DATAJUD_TJGO_ENDPOINT,
                headers={"Authorization": self._authorization},
                json=payload,
            )
        except httpx.TimeoutException:
            error = SourceError(
                SourceErrorCode.NETWORK,
                "A consulta ao DataJud expirou.",
            )
            self._log_failure(error)
            raise error from None
        except httpx.RequestError:
            error = SourceError(
                SourceErrorCode.NETWORK,
                "Não foi possível conectar ao DataJud.",
            )
            self._log_failure(error)
            raise error from None

        status_error = self._status_error(response)
        if status_error is not None:
            self._log_failure(status_error)
            raise status_error

        try:
            response_payload = response.json()
        except ValueError:
            error = SourceError(
                SourceErrorCode.CONTRACT,
                "O DataJud respondeu com JSON inválido.",
                status_code=response.status_code,
            )
            self._log_failure(error)
            raise error from None

        try:
            page = parse_source_page(response_payload, datetime.now(UTC))
        except SourceError as cause:
            error = SourceError(
                SourceErrorCode.CONTRACT,
                cause.message,
                status_code=response.status_code,
            )
            self._log_failure(error)
            raise error from None
        if any(len(hit.sort_values) != len(query.sort) for hit in page.hits):
            error = SourceError(
                SourceErrorCode.CONTRACT,
                "A resposta do DataJud não corresponde à ordenação solicitada.",
                status_code=response.status_code,
            )
            self._log_failure(error)
            raise error
        return page

    def fetch_by_case_number(
        self,
        process_number: str,
        cursor: Cursor | None = None,
        page_size: int = 100,
    ) -> SourcePage:
        """Fetch one exact-CNJ page after local query validation."""

        return self.fetch_page(build_query_by_case_number(process_number), cursor, page_size)

    def close(self) -> None:
        """Close the HTTPX client owned by this adapter."""

        self._client.close()

    def __enter__(self) -> DataJudSourceAdapter:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _status_error(response: httpx.Response) -> SourceError | None:
        status = response.status_code
        if 200 <= status < 300:
            return None
        if status == 401:
            return SourceError(
                SourceErrorCode.AUTHENTICATION,
                "O DataJud recusou a credencial configurada.",
                status_code=status,
            )
        if status == 403:
            return SourceError(
                SourceErrorCode.AUTHORIZATION,
                "O DataJud não autorizou a consulta.",
                status_code=status,
            )
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                retry_after = retry_after.strip()[:128] or None
            return SourceError(
                SourceErrorCode.RATE_LIMIT,
                "O DataJud limitou a frequência de consultas.",
                status_code=status,
                retry_after=retry_after,
            )
        if status in (400, 422):
            return SourceError(
                SourceErrorCode.VALIDATION,
                "O DataJud rejeitou os critérios da consulta.",
                status_code=status,
            )
        if status == 404 or 500 <= status < 600:
            return SourceError(
                SourceErrorCode.SOURCE_UNAVAILABLE,
                "O endpoint público do DataJud está indisponível.",
                status_code=status,
            )
        return SourceError(
            SourceErrorCode.CONTRACT,
            "O DataJud respondeu com um status HTTP fora do contrato.",
            status_code=status,
        )

    @staticmethod
    def _log_failure(error: SourceError) -> None:
        LOGGER.warning(
            "source_request_failed",
            extra={
                "event": "source_request_failed",
                "source": "datajud",
                "error_code": error.code.value,
                "http_status": error.status_code,
            },
        )
