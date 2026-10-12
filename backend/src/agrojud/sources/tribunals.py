"""Versioned DataJud tribunal aliases shared by validation and runtime adapters."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

ALLOWLIST_VERSION = "2026-10-11"
DATAJUD_BASE_URL = "https://api-publica.datajud.cnj.jus.br"


@dataclass(frozen=True, slots=True)
class TribunalEndpoint:
    """One DataJud alias from the bounded CNJ endpoint catalog."""

    alias: str
    canonical_tribunal: str
    justice: str
    endpoint_url: str


_ALIASES = (
    "tjac",
    "tjal",
    "tjam",
    "tjap",
    "tjba",
    "tjce",
    "tjdft",
    "tjes",
    "tjgo",
    "tjma",
    "tjmg",
    "tjms",
    "tjmt",
    "tjpa",
    "tjpb",
    "tjpe",
    "tjpi",
    "tjpr",
    "tjrj",
    "tjrn",
    "tjro",
    "tjrr",
    "tjrs",
    "tjsc",
    "tjse",
    "tjsp",
    "tjto",
    "trf1",
    "trf2",
    "trf3",
    "trf4",
    "trf5",
    "trf6",
)

TRIBUNAL_ALLOWLIST = MappingProxyType(
    {
        alias: TribunalEndpoint(
            alias=alias,
            canonical_tribunal=alias.upper(),
            justice="TRF" if alias.startswith("trf") else "TJDFT" if alias == "tjdft" else "TJ",
            endpoint_url=f"{DATAJUD_BASE_URL}/api_publica_{alias}/_search",
        )
        for alias in _ALIASES
    }
)
_BY_CANONICAL = MappingProxyType(
    {item.canonical_tribunal: item for item in TRIBUNAL_ALLOWLIST.values()}
)


def tribunal_endpoint(value: str) -> TribunalEndpoint:
    """Resolve a canonical sigla or allowlisted endpoint alias, never a URL."""

    if not isinstance(value, str):
        raise ValueError("Tribunal fora da allowlist versionada.")
    normalized = value.strip().casefold()
    endpoint = TRIBUNAL_ALLOWLIST.get(normalized)
    if endpoint is None:
        endpoint = _BY_CANONICAL.get(value.strip().upper())
    if endpoint is None:
        raise ValueError("Tribunal fora da allowlist versionada.")
    return endpoint


def canonicalize_tribunal(value: str) -> str:
    """Return the canonical tribunal sigla for an allowlisted alias or sigla."""

    return tribunal_endpoint(value).canonical_tribunal
