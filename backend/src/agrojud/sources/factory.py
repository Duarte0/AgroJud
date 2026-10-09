"""Environment-gated source selection with no synthetic fallback after errors."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

import httpx

from agrojud.config import Settings
from agrojud.sources.contracts import (
    SourceAdapter,
    SourceError,
    SourceErrorCode,
    SourceQuery,
)
from agrojud.sources.datajud import DataJudSourceAdapter
from agrojud.sources.synthetic import SyntheticQueryFixture, SyntheticSourceAdapter


class SourceKind(StrEnum):
    SYNTHETIC = "synthetic"
    DATAJUD = "datajud"


def build_source_adapter(
    settings: Settings,
    kind: SourceKind,
    *,
    transport: httpx.BaseTransport | None = None,
    fixtures: Mapping[SourceQuery, SyntheticQueryFixture] | None = None,
) -> SourceAdapter:
    """Select one source explicitly and enforce environment boundaries."""

    if settings.environment == "demo":
        if kind is not SourceKind.SYNTHETIC:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O ambiente demo aceita somente a fonte sintética.",
            )
        if transport is not None:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O ambiente demo não aceita transporte HTTP externo.",
            )
        return SyntheticSourceAdapter(fixtures)

    if settings.environment == "real":
        if kind is not SourceKind.DATAJUD:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O ambiente real aceita somente a fonte DataJud.",
            )
        if fixtures is not None:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O ambiente real não aceita fixtures sintéticas.",
            )
        return DataJudSourceAdapter(settings, transport=transport)

    if settings.environment == "test":
        if kind is SourceKind.SYNTHETIC:
            if transport is not None:
                raise SourceError(
                    SourceErrorCode.VALIDATION,
                    "A fonte sintética não aceita transporte HTTP.",
                )
            return SyntheticSourceAdapter(fixtures)
        if transport is None:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O ambiente test exige transporte HTTP simulado para DataJud.",
            )
        if fixtures is not None:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "A fonte DataJud não aceita fixtures sintéticas.",
            )
        return DataJudSourceAdapter(settings, transport=transport)

    raise SourceError(SourceErrorCode.VALIDATION, "O ambiente não seleciona uma fonte compatível.")
