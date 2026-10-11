"""Bounded, sanitized DataJud validation across explicitly selected TJ/TRF endpoints.

This module is deliberately separate from the production TJGO adapter. Its HTTP client
accepts only endpoint aliases in the versioned allowlist below and never touches the
product database or runtime source configuration.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter
from typing import cast
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from agrojud.config import Settings, get_settings
from agrojud.sources.catalog import CatalogItem, FilterDimension, load_catalog
from agrojud.sources.contracts import (
    TIMESTAMP_SORT,
    Cursor,
    SortTerm,
    SourceError,
    SourceErrorCode,
    SourceHit,
    SourcePage,
    parse_source_page,
)
from agrojud.sources.datajud_validation import sanitize_payload, write_report

ALLOWLIST_VERSION = "2026-10-11"
CODE_VERSION = "1.0.0"
DATAJUD_BASE_URL = "https://api-publica.datajud.cnj.jus.br"
ENDPOINT_CATALOG_URL = "https://datajud-wiki.cnj.jus.br/api-publica/endpoints/"
TPU_REFERENCE_URL = "https://www.cnj.jus.br/tabela-processuais-unificadas/"
TERMS_REVIEW_REFERENCE_URLS = (
    "https://www.cnj.jus.br/sistemas/datajud/api-publica/",
    "https://atos.cnj.jus.br/atos/detalhar/3453",
    "https://atos.cnj.jus.br/atos/detalhar/6972",
)
MAX_TRIBUNALS_PER_BATCH = 5
MAX_S1_S2_REQUESTS_PER_TRIBUNAL = 6
MAX_S5_REQUESTS_PER_PAIR = 2
MAX_PAGE_SIZE = 100
MAX_WINDOW_DAYS = 31
S5_MIN_DISTINCT_DOCUMENTS = 3
S5_REQUEST_SPACING_SECONDS = 1.0
PRESET_IDS = (
    "rural.credito_contratos",
    "execucoes.amplas",
    "recuperacao_judicial.por_classe",
    "recuperacao_judicial.por_assunto",
)
_CNJ = re.compile(r"^\d{20}$")
_CNJ_FORMATTED = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")
_STOP_BATCH_CODES = frozenset({SourceErrorCode.AUTHENTICATION, SourceErrorCode.RATE_LIMIT})
_CAPABILITY_NAMES = (
    "http_access",
    "envelope",
    "essential_fields",
    "tribunal_identity",
    "date_filter",
    "query_by_case_number",
    "source_identity",
    "sort_timestamp",
    "compound_sort",
    "pagination",
)


@dataclass(frozen=True, slots=True)
class TribunalEndpoint:
    """One DataJud alias published by the official endpoint catalog."""

    alias: str
    canonical_tribunal: str
    justice: str
    endpoint_url: str


def _make_allowlist() -> Mapping[str, TribunalEndpoint]:
    """Build the dated 33-entry allowlist using the official catalog URL pattern."""

    aliases = (
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
    endpoints: dict[str, TribunalEndpoint] = {}
    for alias in aliases:
        justice = "TRF" if alias.startswith("trf") else "TJDFT" if alias == "tjdft" else "TJ"
        endpoints[alias] = TribunalEndpoint(
            alias=alias,
            canonical_tribunal=alias.upper(),
            justice=justice,
            endpoint_url=f"{DATAJUD_BASE_URL}/api_publica_{alias}/_search",
        )
    return endpoints


TRIBUNAL_ALLOWLIST = _make_allowlist()


class EvidenceState(str):
    """String constants used by capability, applicability, and report states."""

    APPLICABLE = "APPLICABLE"
    VALIDATED = "VALIDATED"
    INCOMPATIBLE = "INCOMPATIBLE"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class ProbeQuery:
    """Probe-only allowlisted query; it does not widen the production TJGO contract."""

    filed_from: date | None = None
    filed_to: date | None = None
    class_codes: tuple[int, ...] = ()
    subject_codes: tuple[int, ...] = ()
    movement_codes: tuple[int, ...] = ()
    process_number: str | None = None
    sort: tuple[SortTerm, ...] = TIMESTAMP_SORT


@dataclass(frozen=True, slots=True)
class DataJudProbeResponse:
    """One parsed response and its HTTP status, with raw values kept in memory only."""

    page: SourcePage
    status_code: int


@dataclass(frozen=True, slots=True)
class TPUApplicability:
    """Human-reviewed TPU/competence evidence for one tribunal/preset pair."""

    state: str
    tpu_version: str | None
    reference_url: str | None
    rationale: str


@dataclass(frozen=True, slots=True)
class RequestResult:
    """Safe request summary plus parsed page held only until classification finishes."""

    page: SourcePage
    status_code: int
    duration_ms: int
    safe_query: object


class DataJudMultiTribunalClient:
    """Single-attempt HTTPX client restricted to aliases in ``TRIBUNAL_ALLOWLIST``."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if settings.environment != "real":
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "A probe multitribunal exige AGROJUD_ENV=real.",
            )
        if settings.datajud_api_key is None:
            raise SourceError(
                SourceErrorCode.AUTHENTICATION,
                "DATAJUD_API_KEY é obrigatória para a probe multitribunal.",
            )
        api_key = settings.datajud_api_key.get_secret_value()
        if not api_key.strip():
            raise SourceError(
                SourceErrorCode.AUTHENTICATION,
                "DATAJUD_API_KEY é obrigatória para a probe multitribunal.",
            )

        self._authorization = f"APIKey {api_key}"
        self._client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(
                connect=5.0,
                read=settings.datajud_read_timeout_seconds,
                write=20.0,
                pool=5.0,
            ),
            follow_redirects=False,
        )

    def fetch_page(
        self,
        alias: str,
        query: ProbeQuery,
        cursor: Cursor | None,
        page_size: int,
    ) -> DataJudProbeResponse:
        """Send one request to an allowlisted endpoint; never retry or follow redirects."""

        endpoint = get_endpoint(alias)
        payload = build_probe_payload(endpoint, query, cursor, page_size)
        try:
            response = self._client.post(
                endpoint.endpoint_url,
                headers={"Authorization": self._authorization},
                json=payload,
            )
        except httpx.TimeoutException:
            raise SourceError(SourceErrorCode.NETWORK, "A consulta ao DataJud expirou.") from None
        except httpx.RequestError:
            raise SourceError(
                SourceErrorCode.NETWORK, "Não foi possível conectar ao DataJud."
            ) from None

        error = _http_status_error(response)
        if error is not None:
            raise error
        try:
            response_payload = response.json()
        except ValueError:
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "O DataJud respondeu com JSON inválido.",
                status_code=response.status_code,
            ) from None
        try:
            page = parse_source_page(response_payload, datetime.now(UTC))
        except SourceError as cause:
            raise SourceError(
                SourceErrorCode.CONTRACT,
                cause.message,
                status_code=response.status_code,
            ) from None
        if len(page.hits) > page_size:
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "O DataJud retornou mais hits que o tamanho solicitado.",
                status_code=response.status_code,
            )
        if any(len(hit.sort_values) != len(query.sort) for hit in page.hits):
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "A resposta do DataJud não corresponde à ordenação solicitada.",
                status_code=response.status_code,
            )
        return DataJudProbeResponse(page=page, status_code=response.status_code)

    def close(self) -> None:
        """Close the HTTPX client owned by this probe."""

        self._client.close()

    def __enter__(self) -> DataJudMultiTribunalClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def get_endpoint(alias: str) -> TribunalEndpoint:
    """Resolve only a known lowercase alias; no caller-provided URL is accepted."""

    try:
        return TRIBUNAL_ALLOWLIST[alias]
    except KeyError, TypeError:
        raise ValueError("Alias DataJud fora da allowlist versionada.") from None


def validate_alias_batch(aliases: Sequence[str]) -> tuple[str, ...]:
    """Validate an explicit batch of one to five unique allowlisted aliases."""

    if isinstance(aliases, (str, bytes)) or not 1 <= len(aliases) <= MAX_TRIBUNALS_PER_BATCH:
        raise ValueError("Informe explicitamente de um a cinco aliases DataJud.")
    if any(not isinstance(alias, str) or alias not in TRIBUNAL_ALLOWLIST for alias in aliases):
        raise ValueError("O lote contém alias fora da allowlist versionada.")
    if len(set(aliases)) != len(aliases):
        raise ValueError("O lote não pode repetir aliases DataJud.")
    return tuple(aliases)


def validate_date_window(filed_from: date, filed_to: date) -> None:
    """Require the operator's explicit manual window and enforce the S5 31-day limit."""

    if type(filed_from) is not date or type(filed_to) is not date or filed_from >= filed_to:
        raise ValueError("Informe uma janela válida com início antes do fim exclusivo.")
    if (filed_to - filed_from).days > MAX_WINDOW_DAYS:
        raise ValueError(f"A janela da probe é limitada a {MAX_WINDOW_DAYS} dias.")


def build_probe_payload(
    endpoint: TribunalEndpoint,
    query: ProbeQuery,
    cursor: Cursor | None,
    page_size: int,
) -> dict[str, object]:
    """Compile the small, fixed DataJud query grammar used by this validation tool."""

    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise ValueError("O tamanho de página deve estar entre 1 e 100.")
    if not query.sort or query.sort[0].field_name != "@timestamp":
        raise ValueError("A ordenação da probe precisa começar por @timestamp.")
    if cursor is not None and (not cursor or len(cursor) != len(query.sort)):
        raise ValueError("O cursor não corresponde à ordenação solicitada.")
    if query.process_number is not None and _CNJ.fullmatch(query.process_number) is None:
        raise ValueError("O CNJ observado não satisfaz o formato esperado.")
    if (query.filed_from is None) != (query.filed_to is None):
        raise ValueError("O intervalo de ajuizamento está incompleto.")
    filters: list[dict[str, object]] = [{"match": {"tribunal": endpoint.canonical_tribunal}}]
    if query.process_number is not None:
        filters.append({"match": {"numeroProcesso": query.process_number}})
    if query.filed_from is not None and query.filed_to is not None:
        if query.filed_from >= query.filed_to:
            raise ValueError("O intervalo de ajuizamento é inválido.")
        filters.append(
            {
                "range": {
                    "dataAjuizamento": {
                        "gte": f"{query.filed_from:%Y%m%d}000000",
                        "lt": f"{query.filed_to:%Y%m%d}000000",
                    }
                }
            }
        )
    if query.class_codes:
        filters.append({"terms": {"classe.codigo": list(query.class_codes)}})
    if query.subject_codes:
        filters.append({"terms": {"assuntos.codigo": list(query.subject_codes)}})
    if query.movement_codes:
        filters.append({"terms": {"movimentos.codigo": list(query.movement_codes)}})
    if (
        query.process_number is None
        and query.filed_from is None
        and not any((query.class_codes, query.subject_codes, query.movement_codes))
    ):
        raise ValueError("A query precisa de filtro além do tribunal.")

    payload: dict[str, object] = {
        "size": page_size,
        "query": {"bool": {"filter": filters}},
        "sort": [{term.field_name: {"order": term.order}} for term in query.sort],
    }
    if cursor is not None:
        if any(
            value is not None and not isinstance(value, (str, int, float, bool)) for value in cursor
        ):
            raise ValueError("O cursor contém valor inválido.")
        payload["search_after"] = list(cursor)
    return payload


def _http_status_error(response: httpx.Response) -> SourceError | None:
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
        "O DataJud respondeu com status HTTP fora do contrato.",
        status_code=status,
    )


def current_discovery_presets() -> tuple[CatalogItem, ...]:
    """Return only the four named discovery presets from the current versioned catalog."""

    catalog = load_catalog()
    selected = {item.id: item for item in catalog.items if item.kind == "preset"}
    missing = set(PRESET_IDS) - set(selected)
    if missing:
        raise ValueError("O catálogo atual não contém todos os quatro presets S5 esperados.")
    return tuple(selected[item_id] for item_id in PRESET_IDS)


def applicability_template(aliases: Sequence[str]) -> dict[str, object]:
    """Create a blank, non-approving TPU review template for selected tribunals."""

    selected = validate_alias_batch(aliases)
    tribunals: dict[str, object] = {}
    for alias in selected:
        tribunals[alias] = {
            preset_id: {
                "state": EvidenceState.INCONCLUSIVE,
                "tpu_version": None,
                "reference_url": None,
                "rationale": "Classificação TPU/competência pendente de revisão oficial.",
            }
            for preset_id in PRESET_IDS
        }
    return {"schema_version": 1, "tribunals": tribunals}


def parse_applicability_document(value: object) -> dict[tuple[str, str], TPUApplicability]:
    """Validate reviewed TPU evidence; missing pairs remain inconclusive and make no calls."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise ValueError("A matriz de aplicabilidade precisa usar schema_version=1.")
    raw_tribunals = value.get("tribunals")
    if not isinstance(raw_tribunals, Mapping):
        raise ValueError("A matriz de aplicabilidade não contém tribunais válidos.")
    known_presets = {item.id for item in current_discovery_presets()}
    parsed: dict[tuple[str, str], TPUApplicability] = {}
    for raw_alias, raw_pairs in raw_tribunals.items():
        if not isinstance(raw_alias, str) or raw_alias not in TRIBUNAL_ALLOWLIST:
            raise ValueError("A matriz TPU contém alias fora da allowlist.")
        if not isinstance(raw_pairs, Mapping):
            raise ValueError("A matriz TPU contém uma lista de presets inválida.")
        unknown = set(raw_pairs) - known_presets
        if unknown:
            raise ValueError("A matriz TPU contém preset fora do catálogo S5 atual.")
        for raw_preset, raw_evidence in raw_pairs.items():
            if not isinstance(raw_preset, str) or not isinstance(raw_evidence, Mapping):
                raise ValueError("A matriz TPU contém uma classificação inválida.")
            state = raw_evidence.get("state")
            rationale = raw_evidence.get("rationale")
            version = raw_evidence.get("tpu_version")
            reference = raw_evidence.get("reference_url")
            if not isinstance(state, str) or state not in {
                EvidenceState.APPLICABLE,
                EvidenceState.NOT_APPLICABLE,
                EvidenceState.INCONCLUSIVE,
            }:
                raise ValueError("Estado de aplicabilidade TPU desconhecido.")
            if not isinstance(rationale, str) or not rationale.strip():
                raise ValueError("Cada classificação TPU exige uma justificativa.")
            normalized_version = _optional_text(version, "tpu_version")
            normalized_reference = _validate_official_reference(reference)
            if state in ("APPLICABLE", EvidenceState.NOT_APPLICABLE) and (
                normalized_version is None or normalized_reference is None
            ):
                raise ValueError(
                    "APPLICABLE/NOT_APPLICABLE exigem versão TPU e referência oficial."
                )
            if (
                state in ("APPLICABLE", EvidenceState.NOT_APPLICABLE)
                and normalized_version != load_catalog().tpu_version
            ):
                raise ValueError(
                    "A classificação precisa usar a versão TPU registrada no catálogo atual."
                )
            parsed[(raw_alias, raw_preset)] = TPUApplicability(
                state=str(state),
                tpu_version=normalized_version,
                reference_url=normalized_reference,
                rationale=rationale.strip(),
            )
    return parsed


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} deve ser texto não vazio ou null.")
    return value.strip()


def _validate_official_reference(value: object) -> str | None:
    reference = _optional_text(value, "reference_url")
    if reference is None:
        return None
    parts = urlsplit(reference)
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or not (host == "cnj.jus.br" or host.endswith(".cnj.jus.br"))
        or parts.username is not None
        or parts.password is not None
        or parts.port is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("A referência TPU precisa ser HTTPS em domínio oficial do CNJ.")
    return reference


def _load_applicability(path: Path | None) -> dict[tuple[str, str], TPUApplicability]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        raise ValueError("Não foi possível ler a matriz TPU local em JSON.") from None
    return parse_applicability_document(value)


def _default_applicability(alias: str, preset_id: str) -> TPUApplicability:
    return TPUApplicability(
        state=EvidenceState.INCONCLUSIVE,
        tpu_version=None,
        reference_url=None,
        rationale=(
            f"Aplicabilidade de {preset_id} para {alias} não foi revisada na matriz TPU; "
            "nenhuma consulta DataJud foi feita."
        ),
    )


class DataJudMultiTribunalProbe:
    """Run isolated S1/S2 endpoint checks and reviewed S5 pairs for a batch of up to five."""

    def __init__(
        self,
        aliases: Sequence[str],
        *,
        filed_from: date,
        filed_to: date,
        applicability: Mapping[tuple[str, str], TPUApplicability] | None = None,
        client: DataJudMultiTribunalClient | None = None,
        preflight_reason: str | None = None,
        environment: str = "not_configured",
        terms_reviewed: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._aliases = validate_alias_batch(aliases)
        validate_date_window(filed_from, filed_to)
        self._filed_from = filed_from
        self._filed_to = filed_to
        self._applicability = applicability or {}
        self._terms_reviewed = terms_reviewed
        self._preflight_reason = preflight_reason
        if client is not None and not terms_reviewed:
            self._client = None
            self._preflight_reason = (
                preflight_reason
                or "Confirme a revisão dos termos/finalidade vigentes antes da probe real."
            )
        else:
            self._client = client
        self._environment = environment
        self._sleep = sleep
        self._stop_reason: str | None = None

    def run(self, *, started_at: datetime | None = None) -> dict[str, object]:
        """Run requested endpoints while retaining independent reports and partial results."""

        started = started_at or datetime.now(UTC)
        run_id = str(uuid.uuid4())
        run_started = perf_counter()
        tribunal_reports: list[dict[str, object]] = []
        for alias in self._aliases:
            if self._stop_reason is not None:
                tribunal_reports.append(self._not_run_tribunal(alias, self._stop_reason))
            else:
                tribunal_reports.append(self._run_tribunal(alias, started_at=started))

        reports_by_alias = {str(report["alias"]): report for report in tribunal_reports}
        s5_counts = {state: 0 for state in _ALL_STATES}
        s1_summary: list[dict[str, object]] = []
        for report in tribunal_reports:
            s1 = cast(dict[str, object], report["s1_s2"])
            s1_summary.append({"alias": report["alias"], "state": s1["aggregate_state"]})
            for pair in cast(list[dict[str, object]], report["s5_pairs"]):
                s5_counts[str(pair["pair_state"])] += 1

        matrix: list[dict[str, object]] = []
        for alias, endpoint in TRIBUNAL_ALLOWLIST.items():
            endpoint_report = reports_by_alias.get(alias)
            if endpoint_report is None:
                matrix.append(
                    {
                        "alias": alias,
                        "canonical_tribunal": endpoint.canonical_tribunal,
                        "justice": endpoint.justice,
                        "s1_s2_state": EvidenceState.NOT_RUN,
                        "s5_pairs": {
                            preset_id: {
                                "applicability_state": EvidenceState.NOT_RUN,
                                "pair_state": EvidenceState.NOT_RUN,
                            }
                            for preset_id in PRESET_IDS
                        },
                    }
                )
                continue
            s1 = cast(dict[str, object], endpoint_report["s1_s2"])
            pair_map = {
                str(pair["preset_id"]): pair
                for pair in cast(list[dict[str, object]], endpoint_report["s5_pairs"])
            }
            s5_matrix: dict[str, dict[str, object]] = {}
            for preset_id in PRESET_IDS:
                pair = pair_map[preset_id]
                applicability_record = cast(dict[str, object], pair["applicability"])
                s5_matrix[preset_id] = {
                    "applicability_state": applicability_record["state"],
                    "pair_state": pair["pair_state"],
                }
            matrix.append(
                {
                    "alias": alias,
                    "canonical_tribunal": endpoint.canonical_tribunal,
                    "justice": endpoint.justice,
                    "s1_s2_state": s1["aggregate_state"],
                    "s5_pairs": s5_matrix,
                }
            )

        summary: dict[str, object] = {
            "report_version": 1,
            "kind": "datajud_multitribunal_batch_summary",
            "run_id": run_id,
            "started_at": started.astimezone(UTC).isoformat(),
            "duration_ms": _elapsed_ms(run_started),
            "code_version": CODE_VERSION,
            "allowlist_version": ALLOWLIST_VERSION,
            "endpoint_catalog_reference": ENDPOINT_CATALOG_URL,
            "terms_review": _terms_review_record(self._terms_reviewed),
            "catalog_version": load_catalog().catalog_version,
            "requested_aliases": list(self._aliases),
            "requested_count": len(self._aliases),
            "request_budget": {
                "max_tribunals": MAX_TRIBUNALS_PER_BATCH,
                "max_s1_s2_requests_per_tribunal": MAX_S1_S2_REQUESTS_PER_TRIBUNAL,
                "max_s5_requests_per_pair": MAX_S5_REQUESTS_PER_PAIR,
                "page_size": MAX_PAGE_SIZE,
                "s5_window_days": (self._filed_to - self._filed_from).days,
                "s5_spacing_seconds": S5_REQUEST_SPACING_SECONDS,
                "automatic_retries": False,
                "writes_to_product_database": False,
            },
            "requests_made": sum(cast(int, report["requests_made"]) for report in tribunal_reports),
            "stop_reason": self._stop_reason,
            "s1_s2_by_tribunal": s1_summary,
            "s5_pair_counts": s5_counts,
            "failures_and_not_run": _batch_exceptions(tribunal_reports),
            "tribunal_reports": [
                {"alias": report["alias"], "file_key": f"tribunal-{report['alias']}-{run_id}.json"}
                for report in tribunal_reports
            ],
            "coverage_matrix": matrix,
            "matrix_review": {"state": "PENDING_HUMAN_REVIEW"},
        }
        return {"summary": summary, "tribunal_reports": tribunal_reports}

    def _run_tribunal(self, alias: str, *, started_at: datetime) -> dict[str, object]:
        endpoint_started = perf_counter()
        endpoint = get_endpoint(alias)
        capabilities = _initial_capabilities(
            self._preflight_reason or "A capacidade ainda não foi observada nesta execução."
        )
        requests: list[dict[str, object]] = []
        repeat_comparison: dict[str, object] | None = None
        s1_query = ProbeQuery(filed_from=self._filed_from, filed_to=self._filed_to)
        first = self._request(alias, "recorte_timestamp_pagina_1", s1_query, None, requests)
        if first is None:
            _assess_request_failure(capabilities, requests[-1] if requests else None)
        else:
            _assess_initial_page(
                alias, endpoint, first.page, self._filed_from, self._filed_to, capabilities
            )

            observed_number = _first_cnj(first.page.hits)
            if observed_number is None:
                _set_capability(
                    capabilities,
                    "query_by_case_number",
                    EvidenceState.INCONCLUSIVE,
                    "Nenhum numeroProcesso válido foi observado para consulta exata.",
                )
            elif self._stop_reason is None and len(requests) < MAX_S1_S2_REQUESTS_PER_TRIBUNAL:
                exact_query = ProbeQuery(process_number=observed_number)
                exact = self._request(alias, "consulta_cnj_observado", exact_query, None, requests)
                _assess_exact_query(exact, observed_number, endpoint, capabilities)

            repeated: RequestResult | None = None
            timestamp_second: RequestResult | None = None
            compound_first: RequestResult | None = None
            compound_second: RequestResult | None = None
            if self._stop_reason is None and len(requests) < MAX_S1_S2_REQUESTS_PER_TRIBUNAL:
                repeated = self._request(
                    alias, "repeticao_recorte_timestamp_pagina_1", s1_query, None, requests
                )
            if repeated is not None:
                repeat_comparison = _repeat_comparison(first.page, repeated.page)
            if (
                self._stop_reason is None
                and first.page.hits
                and first.page.cursor_final is not None
                and len(requests) < MAX_S1_S2_REQUESTS_PER_TRIBUNAL
            ):
                timestamp_second = self._request(
                    alias,
                    "recorte_timestamp_pagina_2",
                    s1_query,
                    first.page.cursor_final,
                    requests,
                )
            _assess_timestamp_sort(first, capabilities)
            if (
                self._stop_reason is None
                and first.page.hits
                and len(requests) < MAX_S1_S2_REQUESTS_PER_TRIBUNAL
            ):
                compound_query = replace(s1_query, sort=(SortTerm(), SortTerm("id.keyword")))
                compound_first = self._request(
                    alias, "recorte_sort_composto_pagina_1", compound_query, None, requests
                )
                _assess_compound_sort(compound_first, capabilities)
                if (
                    compound_first is None
                    and requests[-1].get("error_code") == SourceErrorCode.VALIDATION.value
                ):
                    _set_capability(
                        capabilities,
                        "compound_sort",
                        EvidenceState.INCOMPATIBLE,
                        "O endpoint rejeitou o sort composto candidato.",
                        http_status=requests[-1].get("http_status"),
                    )
            if (
                self._stop_reason is None
                and compound_first is not None
                and compound_first.page.hits
                and compound_first.page.cursor_final is not None
                and len(requests) < MAX_S1_S2_REQUESTS_PER_TRIBUNAL
            ):
                compound_query = replace(s1_query, sort=(SortTerm(), SortTerm("id.keyword")))
                compound_second = self._request(
                    alias,
                    "recorte_sort_composto_pagina_2",
                    compound_query,
                    compound_first.page.cursor_final,
                    requests,
                )
            _assess_pagination(compound_first, compound_second, capabilities)
            _assess_timestamp_pagination(timestamp_second, capabilities)

        s5_pairs = [self._run_s5_pair(alias, item) for item in current_discovery_presets()]
        s1_state = _aggregate_capabilities(capabilities)
        report_requests = len(requests) + sum(
            len(cast(list[dict[str, object]], pair["requests"])) for pair in s5_pairs
        )
        return {
            "report_version": 1,
            "kind": "datajud_multitribunal_endpoint_report",
            "run_id": None,
            "started_at": started_at.astimezone(UTC).isoformat(),
            "duration_ms": _elapsed_ms(endpoint_started),
            "alias": alias,
            "canonical_tribunal": endpoint.canonical_tribunal,
            "justice": endpoint.justice,
            "endpoint": endpoint.endpoint_url,
            "environment": self._environment,
            "code_version": CODE_VERSION,
            "allowlist_version": ALLOWLIST_VERSION,
            "endpoint_catalog_reference": ENDPOINT_CATALOG_URL,
            "terms_review": _terms_review_record(self._terms_reviewed),
            "catalog_version": load_catalog().catalog_version,
            "filed_from": self._filed_from.isoformat(),
            "filed_to_exclusive": self._filed_to.isoformat(),
            "request_budget": {
                "max_s1_s2_requests": MAX_S1_S2_REQUESTS_PER_TRIBUNAL,
                "max_s5_requests_per_pair": MAX_S5_REQUESTS_PER_PAIR,
                "page_size": MAX_PAGE_SIZE,
                "s5_window_days": (self._filed_to - self._filed_from).days,
                "s5_spacing_seconds": S5_REQUEST_SPACING_SECONDS,
                "automatic_retries": False,
                "writes_to_product_database": False,
            },
            "requests_made": report_requests,
            "s1_s2": {
                "requests_made": len(requests),
                "requests": requests,
                "capabilities": capabilities,
                "repeat_comparison": repeat_comparison,
                "aggregate_state": s1_state,
            },
            "s5_pairs": s5_pairs,
            "stop_reason": self._stop_reason,
            "runtime_activation_changed": False,
        }

    def _not_run_tribunal(self, alias: str, reason: str) -> dict[str, object]:
        endpoint = get_endpoint(alias)
        capabilities = _initial_capabilities(reason, state=EvidenceState.NOT_RUN)
        pairs = [
            self._pair_without_call(
                alias,
                item,
                self._get_applicability(alias, item.id),
                reason=reason,
                forced_state=EvidenceState.NOT_RUN,
            )
            for item in current_discovery_presets()
        ]
        return {
            "report_version": 1,
            "kind": "datajud_multitribunal_endpoint_report",
            "run_id": None,
            "started_at": datetime.now(UTC).isoformat(),
            "duration_ms": 0,
            "alias": alias,
            "canonical_tribunal": endpoint.canonical_tribunal,
            "justice": endpoint.justice,
            "endpoint": endpoint.endpoint_url,
            "environment": self._environment,
            "code_version": CODE_VERSION,
            "allowlist_version": ALLOWLIST_VERSION,
            "endpoint_catalog_reference": ENDPOINT_CATALOG_URL,
            "terms_review": _terms_review_record(self._terms_reviewed),
            "catalog_version": load_catalog().catalog_version,
            "filed_from": self._filed_from.isoformat(),
            "filed_to_exclusive": self._filed_to.isoformat(),
            "request_budget": {
                "max_s1_s2_requests": MAX_S1_S2_REQUESTS_PER_TRIBUNAL,
                "max_s5_requests_per_pair": MAX_S5_REQUESTS_PER_PAIR,
                "page_size": MAX_PAGE_SIZE,
                "s5_window_days": (self._filed_to - self._filed_from).days,
                "s5_spacing_seconds": S5_REQUEST_SPACING_SECONDS,
                "automatic_retries": False,
                "writes_to_product_database": False,
            },
            "requests_made": 0,
            "s1_s2": {
                "requests_made": 0,
                "requests": [],
                "capabilities": capabilities,
                "repeat_comparison": None,
                "aggregate_state": EvidenceState.NOT_RUN,
            },
            "s5_pairs": pairs,
            "stop_reason": reason,
            "runtime_activation_changed": False,
        }

    def _request(
        self,
        alias: str,
        name: str,
        query: ProbeQuery,
        cursor: Cursor | None,
        records: list[dict[str, object]],
    ) -> RequestResult | None:
        if self._client is None or self._stop_reason is not None:
            return None
        payload = build_probe_payload(get_endpoint(alias), query, cursor, MAX_PAGE_SIZE)
        safe_query = sanitize_payload(payload)
        request_started = perf_counter()
        try:
            response = self._client.fetch_page(alias, query, cursor, MAX_PAGE_SIZE)
        except SourceError as error:
            records.append(
                {
                    "name": name,
                    "query": safe_query,
                    "outcome": "ERROR",
                    "error_code": error.code.value,
                    "http_status": error.status_code,
                    "duration_ms": _elapsed_ms(request_started),
                }
            )
            if error.code in _STOP_BATCH_CODES:
                self._stop_reason = (
                    f"{error.code.value}: chamadas restantes do lote foram marcadas NOT_RUN."
                )
            return None
        records.append(
            {
                "name": name,
                "query": safe_query,
                "outcome": "SUCCESS",
                "http_status": response.status_code,
                "duration_ms": _elapsed_ms(request_started),
                "response": _response_summary(response.page, query.sort),
            }
        )
        return RequestResult(
            page=response.page,
            status_code=response.status_code,
            duration_ms=_elapsed_ms(request_started),
            safe_query=safe_query,
        )

    def _run_s5_pair(self, alias: str, item: CatalogItem) -> dict[str, object]:
        applicability = self._get_applicability(alias, item.id)
        if applicability.state == EvidenceState.NOT_APPLICABLE:
            return self._pair_without_call(
                alias, item, applicability, reason="Exclusão TPU revisada; sem consulta DataJud."
            )
        if applicability.state != "APPLICABLE":
            return self._pair_without_call(
                alias,
                item,
                applicability,
                reason=_applicability_reason(applicability.state),
                forced_state=EvidenceState.NOT_RUN,
            )
        if self._client is None:
            return self._pair_without_call(
                alias,
                item,
                applicability,
                reason=self._preflight_reason or "A probe não foi autorizada para HTTP.",
            )
        if self._stop_reason is not None:
            return self._pair_without_call(
                alias,
                item,
                applicability,
                reason=self._stop_reason,
                forced_state=EvidenceState.NOT_RUN,
            )

        query = _preset_query(item, self._filed_from, self._filed_to)
        records: list[dict[str, object]] = []
        hits: list[SourceHit] = []
        total: dict[str, object] | None = None
        failure: dict[str, object] | None = None
        cursor: Cursor | None = None
        for page_number in range(1, MAX_S5_REQUESTS_PER_PAIR + 1):
            if page_number > 1:
                self._sleep(S5_REQUEST_SPACING_SECONDS)
            result = self._request(
                alias,
                f"s5_{item.id.replace('.', '_')}_pagina_{page_number}",
                query,
                cursor,
                records,
            )
            if result is None:
                if records and records[-1].get("outcome") == "ERROR":
                    failure = records[-1]
                break
            if page_number == 1:
                total = {"value": result.page.total_value, "relation": result.page.total_relation}
            hits.extend(result.page.hits)
            if (
                _distinct_matching_documents(item, hits) >= S5_MIN_DISTINCT_DOCUMENTS
                or len(result.page.hits) < MAX_PAGE_SIZE
                or result.page.cursor_final is None
            ):
                break
            cursor = result.page.cursor_final

        query_status, sample_status = _classify_s5(item, hits, failure)
        return {
            "preset_id": item.id,
            "preset_version": item.version,
            "catalog_version": load_catalog().catalog_version,
            "applicability": _applicability_record(applicability),
            "filed_from": self._filed_from.isoformat(),
            "filed_to_exclusive": self._filed_to.isoformat(),
            "total_observed": total,
            "requests_made": len(records),
            "request_budget": {
                "max_requests": MAX_S5_REQUESTS_PER_PAIR,
                "page_size": MAX_PAGE_SIZE,
                "spacing_seconds": S5_REQUEST_SPACING_SECONDS,
                "automatic_retries": False,
            },
            "requests": records,
            "query_status": query_status,
            "sample_status": sample_status,
            "pair_state": _aggregate_pair(applicability.state, query_status, sample_status),
            "runtime_activation_changed": False,
        }

    def _pair_without_call(
        self,
        alias: str,
        item: CatalogItem,
        applicability: TPUApplicability,
        *,
        reason: str,
        forced_state: str | None = None,
    ) -> dict[str, object]:
        if applicability.state == EvidenceState.NOT_APPLICABLE:
            query_state = sample_state = EvidenceState.NOT_APPLICABLE
            pair_state = EvidenceState.NOT_APPLICABLE
        elif forced_state == EvidenceState.NOT_RUN:
            query_state = sample_state = EvidenceState.NOT_RUN
            pair_state = (
                EvidenceState.INCONCLUSIVE
                if applicability.state == EvidenceState.INCONCLUSIVE
                else EvidenceState.NOT_RUN
            )
        else:
            query_state = sample_state = EvidenceState.INCONCLUSIVE
            pair_state = EvidenceState.INCONCLUSIVE
        safe_reason = _sanitize_rationale(reason)
        inconclusive = {"state": query_state, "evidence": {"reason": safe_reason}}
        return {
            "preset_id": item.id,
            "preset_version": item.version,
            "catalog_version": load_catalog().catalog_version,
            "applicability": _applicability_record(applicability),
            "filed_from": self._filed_from.isoformat(),
            "filed_to_exclusive": self._filed_to.isoformat(),
            "total_observed": None,
            "requests_made": 0,
            "request_budget": {
                "max_requests": MAX_S5_REQUESTS_PER_PAIR,
                "page_size": MAX_PAGE_SIZE,
                "spacing_seconds": S5_REQUEST_SPACING_SECONDS,
                "automatic_retries": False,
            },
            "requests": [],
            "query_status": inconclusive,
            "sample_status": {"state": sample_state, "evidence": {"reason": safe_reason}},
            "pair_state": pair_state,
            "runtime_activation_changed": False,
        }

    def _get_applicability(self, alias: str, preset_id: str) -> TPUApplicability:
        return self._applicability.get((alias, preset_id), _default_applicability(alias, preset_id))


_ALL_STATES = (
    EvidenceState.VALIDATED,
    EvidenceState.INCOMPATIBLE,
    EvidenceState.INCONCLUSIVE,
    EvidenceState.NOT_RUN,
    EvidenceState.NOT_APPLICABLE,
)


def _preset_query(item: CatalogItem, filed_from: date, filed_to: date) -> ProbeQuery:
    return ProbeQuery(
        filed_from=filed_from,
        filed_to=filed_to,
        class_codes=tuple(item.filters["class"]),
        subject_codes=tuple(item.filters["subject"]),
        movement_codes=tuple(item.filters["movement"]),
        sort=TIMESTAMP_SORT,
    )


def _initial_capabilities(
    reason: str, *, state: str = EvidenceState.INCONCLUSIVE
) -> dict[str, dict[str, object]]:
    return {name: {"state": state, "evidence": {"reason": reason}} for name in _CAPABILITY_NAMES}


def _set_capability(
    capabilities: dict[str, dict[str, object]],
    name: str,
    state: str,
    reason: str,
    **evidence: object,
) -> None:
    capabilities[name] = {"state": state, "evidence": {"reason": reason, **evidence}}


def _assess_request_failure(
    capabilities: dict[str, dict[str, object]], request: Mapping[str, object] | None
) -> None:
    if request is None:
        return
    error_code = request.get("error_code")
    status = request.get("http_status")
    if error_code in (SourceErrorCode.AUTHENTICATION.value, SourceErrorCode.AUTHORIZATION.value):
        access_state = EvidenceState.INCOMPATIBLE
    else:
        access_state = EvidenceState.INCONCLUSIVE
    _set_capability(
        capabilities,
        "http_access",
        access_state,
        "A resposta HTTP não confirmou acesso utilizável ao endpoint.",
        error_code=error_code,
        http_status=status,
    )
    envelope_state = (
        EvidenceState.INCOMPATIBLE
        if error_code == SourceErrorCode.CONTRACT.value
        else EvidenceState.INCONCLUSIVE
    )
    _set_capability(
        capabilities,
        "envelope",
        envelope_state,
        "A resposta não forneceu envelope DataJud utilizável.",
        error_code=error_code,
    )
    if error_code == SourceErrorCode.VALIDATION.value:
        _set_capability(
            capabilities,
            "date_filter",
            EvidenceState.INCOMPATIBLE,
            "O endpoint rejeitou o recorte de data enviado.",
            http_status=status,
        )


def _assess_initial_page(
    alias: str,
    endpoint: TribunalEndpoint,
    page: SourcePage,
    filed_from: date,
    filed_to: date,
    capabilities: dict[str, dict[str, object]],
) -> None:
    _set_capability(
        capabilities,
        "http_access",
        EvidenceState.VALIDATED,
        "O endpoint respondeu HTTP 2xx à consulta limitada.",
    )
    _set_capability(
        capabilities,
        "envelope",
        EvidenceState.VALIDATED,
        "Envelope hits/hits e sort aceitos pelo parser de SPEC-002.",
        hit_count=len(page.hits),
    )
    if not page.hits:
        for name in ("essential_fields", "tribunal_identity", "date_filter", "source_identity"):
            _set_capability(
                capabilities,
                name,
                EvidenceState.INCONCLUSIVE,
                "A resposta não teve amostra; resultado vazio não valida nem invalida o filtro.",
                hit_count=0,
            )
        return

    essential_valid = 0
    tribunal_valid = 0
    date_valid = 0
    identity_valid = 0
    observed_dates: list[date] = []
    for hit in page.hits:
        source = _source(hit)
        number = source.get("numeroProcesso") if source is not None else None
        filed_date = (
            _parse_source_date(source.get("dataAjuizamento")) if source is not None else None
        )
        source_id = source.get("id") if source is not None else None
        raw_id = hit.raw.get("_id")
        if (
            source is not None
            and isinstance(number, str)
            and _CNJ.fullmatch(number) is not None
            and filed_date is not None
            and isinstance(source_id, str)
            and bool(source_id.strip())
            and isinstance(raw_id, str)
            and bool(raw_id.strip())
        ):
            essential_valid += 1
        if source is not None and source.get("tribunal") == endpoint.canonical_tribunal:
            tribunal_valid += 1
        if filed_date is not None:
            observed_dates.append(filed_date)
            if filed_from <= filed_date < filed_to:
                date_valid += 1
        if (
            isinstance(raw_id, str)
            and bool(raw_id.strip())
            and isinstance(source_id, str)
            and bool(source_id.strip())
            and raw_id == source_id
        ):
            identity_valid += 1

    count = len(page.hits)
    _set_capability(
        capabilities,
        "essential_fields",
        EvidenceState.VALIDATED if essential_valid == count else EvidenceState.INCOMPATIBLE,
        "Campos essenciais verificados em todos os hits observados.",
        valid_hits=essential_valid,
        sample_count=count,
    )
    _set_capability(
        capabilities,
        "tribunal_identity",
        EvidenceState.VALIDATED if tribunal_valid == count else EvidenceState.INCOMPATIBLE,
        "A sigla retornada foi comparada com a sigla canônica do alias consultado.",
        matching_hits=tribunal_valid,
        sample_count=count,
    )
    _set_capability(
        capabilities,
        "date_filter",
        EvidenceState.VALIDATED if date_valid == count else EvidenceState.INCOMPATIBLE,
        "A data de ajuizamento de cada hit foi comparada com [início, fim exclusivo).",
        matching_hits=date_valid,
        sample_count=count,
        min_observed_date=min(observed_dates).isoformat() if observed_dates else None,
        max_observed_date=max(observed_dates).isoformat() if observed_dates else None,
    )
    _set_capability(
        capabilities,
        "source_identity",
        EvidenceState.VALIDATED if identity_valid == count else EvidenceState.INCOMPATIBLE,
        "_id e _source.id foram comparados sem registrar os valores.",
        matching_hits=identity_valid,
        sample_count=count,
    )


def _assess_exact_query(
    result: RequestResult | None,
    observed_number: str,
    endpoint: TribunalEndpoint,
    capabilities: dict[str, dict[str, object]],
) -> None:
    if result is None:
        _set_capability(
            capabilities,
            "query_by_case_number",
            EvidenceState.INCONCLUSIVE,
            "A consulta exata não foi concluída nesta execução.",
        )
        return
    if not result.page.hits:
        _set_capability(
            capabilities,
            "query_by_case_number",
            EvidenceState.INCONCLUSIVE,
            "A consulta exata não retornou amostra; não se inferiu ausência.",
            hit_count=0,
        )
        return
    matching = 0
    for hit in result.page.hits:
        source = _source(hit)
        if (
            source is not None
            and source.get("numeroProcesso") == observed_number
            and source.get("tribunal") == endpoint.canonical_tribunal
        ):
            matching += 1
    state = (
        EvidenceState.VALIDATED if matching == len(result.page.hits) else EvidenceState.INCOMPATIBLE
    )
    _set_capability(
        capabilities,
        "query_by_case_number",
        state,
        "Consulta construída apenas com CNJ de hit público observado nesta execução.",
        matching_hits=matching,
        sample_count=len(result.page.hits),
    )


def _assess_timestamp_sort(
    result: RequestResult,
    capabilities: dict[str, dict[str, object]],
) -> None:
    ordered, _ = _sort_observation(result.page.hits, 1)
    if len(result.page.hits) < 2:
        state = EvidenceState.INCONCLUSIVE
        reason = "Sort @timestamp aceito, mas não houve dois hits para observar ordem."
    elif not ordered:
        state = EvidenceState.INCOMPATIBLE
        reason = "Sort @timestamp retornou valores inválidos ou regressivos na amostra."
    else:
        state = EvidenceState.VALIDATED
        reason = "Sort @timestamp foi observado em ordem ascendente na amostra."
    _set_capability(
        capabilities,
        "sort_timestamp",
        state,
        reason,
        sample_count=len(result.page.hits),
        duplicate_primary_values=_primary_tie_count(result.page.hits),
    )


def _assess_compound_sort(
    result: RequestResult | None,
    capabilities: dict[str, dict[str, object]],
) -> None:
    if result is None:
        return
    hits = result.page.hits
    ordered, _ = _sort_observation(hits, 2)
    if not hits or len(hits) < 2:
        state = EvidenceState.INCONCLUSIVE
        reason = "Sort composto sem duas amostras não valida a ordenação candidata."
    elif not ordered:
        state = EvidenceState.INCOMPATIBLE
        reason = "Sort @timestamp + id.keyword retornou shape/ordem incompatível."
    else:
        state = EvidenceState.VALIDATED
        reason = "Sort composto foi aceito e observado em ordem ascendente na amostra."
    _set_capability(
        capabilities,
        "compound_sort",
        state,
        reason,
        sample_count=len(hits),
        duplicate_primary_values=_primary_tie_count(hits),
    )


def _assess_pagination(
    first: RequestResult | None,
    second: RequestResult | None,
    capabilities: dict[str, dict[str, object]],
) -> None:
    if first is None or second is None:
        _set_capability(
            capabilities,
            "pagination",
            EvidenceState.INCONCLUSIVE,
            "Duas páginas compostas não foram observadas dentro do orçamento.",
        )
        return
    left = first.page
    right = second.page
    evidence: dict[str, object] = {
        "first_page_hits": len(left.hits),
        "second_page_hits": len(right.hits),
        "two_non_empty_pages": bool(left.hits and right.hits),
        "cursor_advanced": bool(
            left.cursor_final is not None
            and right.cursor_final is not None
            and left.cursor_final != right.cursor_final
        ),
    }
    if not left.hits or not right.hits or left.cursor_final is None or right.cursor_final is None:
        _set_capability(
            capabilities,
            "pagination",
            EvidenceState.INCONCLUSIVE,
            "Duas páginas não vazias e cursores não foram observados.",
            **evidence,
        )
        return
    first_ids = _document_id_list(left.hits)
    second_ids = _document_id_list(right.hits)
    first_id_set = set(first_ids)
    second_id_set = set(second_ids)
    overlap = len(first_id_set.intersection(second_id_set))
    duplicate_count = (
        len(first_ids) - len(first_id_set) + len(second_ids) - len(second_id_set) + overlap
    )
    ordered_first, _ = _sort_observation(left.hits, 2)
    ordered_second, _ = _sort_observation(right.hits, 2)
    boundary = _compare_sort_tuples(left.hits[-1].sort_values, right.hits[0].sort_values)
    evidence.update(
        {
            "overlap_by_source_document_id": overlap,
            "duplicate_ids_within_or_across_pages": duplicate_count,
            "ordered_within_pages": ordered_first and ordered_second,
            "ordered_across_boundary": boundary is not None and boundary < 0,
            "timestamp_ties_observed": _primary_tie_count(left.hits + right.hits),
        }
    )
    if (
        not evidence["cursor_advanced"]
        or duplicate_count > 0
        or not ordered_first
        or not ordered_second
        or boundary is not None
        and boundary > 0
    ):
        state = EvidenceState.INCOMPATIBLE
        reason = "Cursor, sobreposição ou ordem contradiz a paginação observada."
    elif boundary == 0:
        state = EvidenceState.INCONCLUSIVE
        reason = "A fronteira empatou; a amostra não demonstrou avanço estrito do sort composto."
    else:
        state = EvidenceState.VALIDATED
        reason = "Duas páginas não vazias avançaram, ordenadas e sem sobreposição observada."
    _set_capability(capabilities, "pagination", state, reason, **evidence)


def _assess_timestamp_pagination(
    second: RequestResult | None,
    capabilities: dict[str, dict[str, object]],
) -> None:
    if second is None:
        return
    evidence = capabilities["pagination"]["evidence"]
    if isinstance(evidence, dict):
        evidence["timestamp_second_page_observed"] = bool(second.page.hits)


def _classify_s5(
    item: CatalogItem,
    hits: Sequence[SourceHit],
    failure: Mapping[str, object] | None,
) -> tuple[dict[str, object], dict[str, object]]:
    if not hits:
        reason = (
            "A consulta não retornou resposta utilizável; sem inferência."
            if failure is not None
            else "A janela não teve hits observados; ausência não prova incompatibilidade."
        )
        inconclusive: dict[str, object] = {
            "state": EvidenceState.INCONCLUSIVE,
            "evidence": {"reason": reason},
        }
        return inconclusive, dict(inconclusive)

    matching = 0
    mismatched = 0
    matched_codes: dict[str, dict[str, int]] = {}
    matching_ids: set[str] = set()
    for hit in hits:
        source = _source(hit)
        if source is None:
            mismatched += 1
            continue
        dimensions: tuple[tuple[FilterDimension, tuple[int, ...], str], ...] = (
            ("class", tuple(item.filters["class"]), "classe"),
            ("subject", tuple(item.filters["subject"]), "assuntos"),
            ("movement", tuple(item.filters["movement"]), "movimentos"),
        )
        hit_matches = True
        for dimension, requested, field in dimensions:
            if not requested:
                continue
            found = set(_nested_codes(source.get(field))).intersection(requested)
            if not found:
                hit_matches = False
                continue
            counts = matched_codes.setdefault(dimension, {})
            for code in found:
                key = str(code)
                counts[key] = counts.get(key, 0) + 1
        if hit_matches:
            matching += 1
            source_key: object = hit.raw.get("_id")
            if not isinstance(source_key, str) or not source_key:
                source_key = source.get("id")
            if isinstance(source_key, str) and source_key:
                matching_ids.add(source_key)
        else:
            mismatched += 1

    query_state = EvidenceState.INCOMPATIBLE if mismatched else EvidenceState.VALIDATED
    query_status: dict[str, object] = {
        "state": query_state,
        "evidence": {
            "sample_count": len(hits),
            "matching_hits": matching,
            "mismatched_hits": mismatched,
            "matched_codes": matched_codes,
            "reason": (
                "Há hit observado sem código pedido em dimensão filtrada."
                if mismatched
                else "Todos os hits observados contêm código pedido por dimensão filtrada."
            ),
        },
    }
    distinct = len(matching_ids)
    sample_state = (
        EvidenceState.INCOMPATIBLE
        if mismatched
        else EvidenceState.VALIDATED
        if distinct >= S5_MIN_DISTINCT_DOCUMENTS
        else EvidenceState.INCONCLUSIVE
    )
    sample_status: dict[str, object] = {
        "state": sample_state,
        "evidence": {
            "distinct_matching_documents": distinct,
            "minimum_required": S5_MIN_DISTINCT_DOCUMENTS,
            "matched_codes": matched_codes,
            "reason": (
                "Foi observada resposta incompatível com os códigos solicitados."
                if mismatched
                else "Amostra estruturada suficiente observada."
                if distinct >= S5_MIN_DISTINCT_DOCUMENTS
                else "Menos de três documentos distintos; a amostra fica inconclusiva."
            ),
        },
    }
    return query_status, sample_status


def _distinct_matching_documents(item: CatalogItem, hits: Sequence[SourceHit]) -> int:
    keys: set[str] = set()
    for hit in hits:
        source = _source(hit)
        if source is None:
            continue
        matches = True
        dimensions: tuple[tuple[FilterDimension, str], ...] = (
            ("class", "classe"),
            ("subject", "assuntos"),
            ("movement", "movimentos"),
        )
        for dimension, field in dimensions:
            requested = tuple(item.filters[dimension])
            if requested and not set(_nested_codes(source.get(field))).intersection(requested):
                matches = False
                break
        if matches:
            key: object = hit.raw.get("_id")
            if not isinstance(key, str) or not key:
                key = source.get("id")
            if isinstance(key, str) and key:
                keys.add(key)
    return len(keys)


def _nested_codes(value: object) -> set[int]:
    if isinstance(value, Mapping):
        code = value.get("codigo")
        if isinstance(code, int) and not isinstance(code, bool):
            return {code}
        if isinstance(code, str) and code.isdigit():
            return {int(code)}
        return set()
    if isinstance(value, list):
        result: set[int] = set()
        for nested in value:
            result.update(_nested_codes(nested))
        return result
    return set()


def _source(hit: SourceHit) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], hit.source) if isinstance(hit.source, Mapping) else None


def _first_cnj(hits: Sequence[SourceHit]) -> str | None:
    for hit in hits:
        source = _source(hit)
        candidate = source.get("numeroProcesso") if source is not None else None
        if isinstance(candidate, str) and _CNJ.fullmatch(candidate):
            return candidate
    return None


def _parse_source_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if re.fullmatch(r"\d{14}", candidate):
        try:
            return datetime.strptime(candidate, "%Y%m%d%H%M%S").date()
        except ValueError:
            return None
    try:
        return date.fromisoformat(candidate[:10])
    except ValueError:
        return None


def _response_summary(page: SourcePage, sort: Sequence[SortTerm]) -> dict[str, object]:
    shape_paths = ("numeroProcesso", "tribunal", "id", "dataAjuizamento")
    field_shapes: dict[str, dict[str, object]] = {}
    for path in shape_paths:
        values: list[object] = []
        for hit in page.hits:
            source = _source(hit)
            value = source.get(path) if source is not None else None
            if value is not None:
                values.append(value)
        field_shapes[path] = {
            "present_count": len(values),
            "sample_count": len(page.hits),
            "types": sorted({_json_type(value) for value in values}),
        }
    sort_types = [
        sorted(
            {
                _json_type(hit.sort_values[index])
                for hit in page.hits
                if len(hit.sort_values) > index
            }
        )
        for index, _term in enumerate(sort)
    ]
    ordered, _ = _sort_observation(page.hits, len(sort))
    return {
        "hit_count": len(page.hits),
        "total_value": page.total_value,
        "total_relation": page.total_relation,
        "field_shapes": field_shapes,
        "sort_received": {
            "fields": [term.field_name for term in sort],
            "values_per_hit": sorted({len(hit.sort_values) for hit in page.hits}),
            "value_types_by_position": sort_types,
            "ordered_ascending_in_sample": ordered,
            "duplicate_primary_values": _primary_tie_count(page.hits),
        },
    }


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "other"


def _sort_observation(hits: Sequence[SourceHit], field_count: int) -> tuple[bool, int]:
    if not hits or any(len(hit.sort_values) != field_count for hit in hits):
        return False, 0
    rows = [hit.sort_values for hit in hits]
    for row in rows:
        if not isinstance(row[0], (int, float)) or isinstance(row[0], bool):
            return False, _primary_tie_count(hits)
        if field_count == 2 and not isinstance(row[1], str):
            return False, _primary_tie_count(hits)
    for previous, current in zip(rows, rows[1:], strict=False):
        comparison = _compare_sort_tuples(previous, current)
        if comparison is None or comparison > 0 or field_count == 2 and comparison == 0:
            return False, _primary_tie_count(hits)
    return True, _primary_tie_count(hits)


def _compare_sort_tuples(left: Cursor, right: Cursor) -> int | None:
    if len(left) != len(right):
        return None
    for left_value, right_value in zip(left, right, strict=True):
        if (
            isinstance(left_value, (int, float))
            and not isinstance(left_value, bool)
            and isinstance(right_value, (int, float))
            and not isinstance(right_value, bool)
        ):
            if left_value < right_value:
                return -1
            if left_value > right_value:
                return 1
            continue
        if isinstance(left_value, str) and isinstance(right_value, str):
            if left_value < right_value:
                return -1
            if left_value > right_value:
                return 1
            continue
        return None
    return 0


def _primary_tie_count(hits: Sequence[SourceHit]) -> int:
    values = [hit.sort_values[0] for hit in hits if hit.sort_values]
    return len(values) - len(set(values))


def _document_ids(hits: Sequence[SourceHit]) -> set[str]:
    return set(_document_id_list(hits))


def _document_id_list(hits: Sequence[SourceHit]) -> list[str]:
    return [hit.source_id for hit in hits if isinstance(hit.source_id, str) and bool(hit.source_id)]


def _repeat_comparison(first: SourcePage, repeated: SourcePage) -> dict[str, object]:
    first_ids = _document_ids(first.hits)
    repeated_ids = _document_ids(repeated.hits)
    return {
        "first_page_hits": len(first.hits),
        "repeated_page_hits": len(repeated.hits),
        "overlap_by_source_document_id": len(first_ids.intersection(repeated_ids)),
        "same_hit_count": len(first.hits) == len(repeated.hits),
        "same_cursor": first.cursor_final == repeated.cursor_final,
        "interpretation": "Duas leituras não representam snapshot consistente da fonte.",
    }


def _aggregate_capabilities(capabilities: Mapping[str, Mapping[str, object]]) -> str:
    states = {str(value.get("state")) for value in capabilities.values()}
    if states == {EvidenceState.NOT_RUN}:
        return EvidenceState.NOT_RUN
    if EvidenceState.INCOMPATIBLE in states:
        return EvidenceState.INCOMPATIBLE
    if all(state == EvidenceState.VALIDATED for state in states):
        return EvidenceState.VALIDATED
    return EvidenceState.INCONCLUSIVE


def _batch_exceptions(reports: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    exceptions: list[dict[str, object]] = []
    for report in reports:
        s1 = cast(dict[str, object], report["s1_s2"])
        failed_requests = [
            {
                "name": request.get("name"),
                "error_code": request.get("error_code"),
                "http_status": request.get("http_status"),
            }
            for request in cast(list[dict[str, object]], s1.get("requests", []))
            if request.get("outcome") == "ERROR"
        ]
        failed_s5_requests = [
            {
                "preset_id": pair["preset_id"],
                "name": request.get("name"),
                "error_code": request.get("error_code"),
                "http_status": request.get("http_status"),
            }
            for pair in cast(list[dict[str, object]], report["s5_pairs"])
            for request in cast(list[dict[str, object]], pair["requests"])
            if request.get("outcome") == "ERROR"
        ]
        not_run_pairs = [
            str(pair["preset_id"])
            for pair in cast(list[dict[str, object]], report["s5_pairs"])
            if pair.get("pair_state") == EvidenceState.NOT_RUN
        ]
        if (
            failed_requests
            or failed_s5_requests
            or not_run_pairs
            or report.get("stop_reason") is not None
        ):
            exceptions.append(
                {
                    "alias": report["alias"],
                    "stop_reason": report.get("stop_reason"),
                    "failed_requests": failed_requests,
                    "failed_s5_requests": failed_s5_requests,
                    "s5_pairs_not_run": not_run_pairs,
                }
            )
    return exceptions


def _applicability_record(value: TPUApplicability) -> dict[str, object]:
    return {
        "state": value.state,
        "tpu_version": value.tpu_version,
        "reference_url": value.reference_url,
        "rationale": _applicability_reason(value.state),
    }


def _applicability_reason(state: str) -> str:
    """Use report-safe rationale text; never copy operator free text into reports."""

    if state == EvidenceState.APPLICABLE:
        return "Aplicabilidade confirmada na revisão TPU/competência registrada."
    if state == EvidenceState.NOT_APPLICABLE:
        return "Inaplicabilidade confirmada na revisão TPU/competência registrada."
    return "Evidência TPU/competência insuficiente; nenhuma consulta S5 foi feita."


def _terms_review_record(confirmed: bool) -> dict[str, object]:
    return {
        "operator_confirmed_before_probe": confirmed,
        "references": list(TERMS_REVIEW_REFERENCE_URLS),
    }


def _sanitize_rationale(value: str) -> str:
    sanitized = re.sub(r"\b\d{20}\b", "<identificador-redigido>", value)
    sanitized = _CNJ_FORMATTED.sub("<identificador-redigido>", sanitized)
    return " ".join(sanitized.split())[:500]


def _aggregate_pair(
    applicability: str, query_status: Mapping[str, object], sample_status: Mapping[str, object]
) -> str:
    if applicability == EvidenceState.NOT_APPLICABLE:
        return EvidenceState.NOT_APPLICABLE
    if applicability != "APPLICABLE":
        return EvidenceState.INCONCLUSIVE
    states = {str(query_status.get("state")), str(sample_status.get("state"))}
    if EvidenceState.INCOMPATIBLE in states:
        return EvidenceState.INCOMPATIBLE
    if states == {EvidenceState.VALIDATED}:
        return EvidenceState.VALIDATED
    if EvidenceState.NOT_RUN in states:
        return EvidenceState.NOT_RUN
    return EvidenceState.INCONCLUSIVE


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Informe a data no formato AAAA-MM-DD.") from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Executa validação DataJud limitada por tribunal; exige aliases explícitos, "
            "janela manual e revisão dos termos antes de probes reais."
        )
    )
    parser.add_argument("aliases", nargs="+", help="De um a cinco aliases allowlisted.")
    parser.add_argument("--from-date", type=_parse_iso_date, help="Início inclusivo AAAA-MM-DD.")
    parser.add_argument("--to-date", type=_parse_iso_date, help="Fim exclusivo AAAA-MM-DD.")
    parser.add_argument("--applicability", type=Path, help="Matriz TPU revisada em JSON.")
    parser.add_argument("--output-dir", type=Path, help="Diretório dos relatórios JSON.")
    parser.add_argument(
        "--template-out",
        type=Path,
        help="Gera matriz TPU em branco para os aliases informados, sem chamadas externas.",
    )
    parser.add_argument(
        "--confirm-terms-reviewed",
        action="store_true",
        help="Confirma revisão dos termos/finalidade vigentes e autorização prévia quando exigida.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; refuses remote calls unless all explicit operational gates pass."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        aliases = validate_alias_batch(args.aliases)
    except ValueError as error:
        parser.error(str(error))
    if args.template_out is not None:
        args.template_out.parent.mkdir(parents=True, exist_ok=True)
        args.template_out.write_text(
            json.dumps(applicability_template(aliases), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Matriz TPU em branco criada sem chamadas externas: {args.template_out}")
        return 0
    if args.from_date is None or args.to_date is None:
        parser.error(
            "--from-date e --to-date são obrigatórios; nenhuma janela histórica é escolhida "
            "automaticamente."
        )
    try:
        validate_date_window(args.from_date, args.to_date)
        applicability = _load_applicability(args.applicability)
    except ValueError as error:
        parser.error(str(error))
    if args.output_dir is None:
        parser.error("--output-dir é obrigatório para salvar relatórios separados.")

    settings: Settings | None
    preflight_reason: str | None = None
    client: DataJudMultiTribunalClient | None = None
    try:
        settings = get_settings()
    except ValidationError:
        settings = None
        preflight_reason = "Configuração do backend inválida; nenhuma solicitação HTTP foi enviada."
    environment = settings.environment if settings is not None else "invalid"
    if settings is not None:
        if settings.environment != "real":
            preflight_reason = "AGROJUD_ENV deve ser real; nenhuma solicitação HTTP foi enviada."
        elif (
            settings.datajud_api_key is None
            or not settings.datajud_api_key.get_secret_value().strip()
        ):
            preflight_reason = "DATAJUD_API_KEY ausente; nenhuma solicitação HTTP foi enviada."
        elif not args.confirm_terms_reviewed:
            preflight_reason = (
                "Confirme a revisão dos termos/finalidade vigentes antes da probe real; "
                "nenhuma solicitação HTTP foi enviada."
            )
        else:
            try:
                client = DataJudMultiTribunalClient(settings)
            except SourceError as error:
                preflight_reason = error.message

    started = datetime.now(UTC)
    with client if client is not None else _NoopContext() as active_client:
        probe = DataJudMultiTribunalProbe(
            aliases,
            filed_from=args.from_date,
            filed_to=args.to_date,
            applicability=applicability,
            client=active_client,
            preflight_reason=preflight_reason,
            environment=environment,
            terms_reviewed=args.confirm_terms_reviewed,
        )
        report = probe.run(started_at=started)
    _write_batch_reports(report, args.output_dir)
    summary = cast(dict[str, object], report["summary"])
    print(
        f"Probe multitribunal: {summary['requests_made']} requisição(ões) em "
        f"{len(aliases)} tribunal(is). Relatórios: {args.output_dir}"
    )
    if preflight_reason is not None:
        print(preflight_reason, file=sys.stderr)
        return 2
    any_error = any(
        request.get("outcome") == "ERROR"
        for endpoint_report in cast(list[dict[str, object]], report["tribunal_reports"])
        for request in _all_request_records(endpoint_report)
    )
    any_incompatible = any(
        result.get("state") == EvidenceState.INCOMPATIBLE
        for endpoint_report in cast(list[dict[str, object]], report["tribunal_reports"])
        for result in _s1_capability_values(endpoint_report)
    )
    return 1 if any_error or any_incompatible else 0


class _NoopContext:
    """Context manager matching the real client's lifecycle when preflight blocks HTTP."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> None:
        return None


def _all_request_records(endpoint_report: Mapping[str, object]) -> list[dict[str, object]]:
    s1 = cast(dict[str, object], endpoint_report["s1_s2"])
    s1_records = cast(list[dict[str, object]], s1["requests"])
    s5_records = [
        request
        for pair in cast(list[dict[str, object]], endpoint_report["s5_pairs"])
        for request in cast(list[dict[str, object]], pair["requests"])
    ]
    return [*s1_records, *s5_records]


def _s1_capability_values(endpoint_report: Mapping[str, object]) -> Sequence[dict[str, object]]:
    s1 = cast(dict[str, object], endpoint_report["s1_s2"])
    return tuple(cast(dict[str, dict[str, object]], s1["capabilities"]).values())


def _write_batch_reports(report: Mapping[str, object], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = cast(dict[str, object], report["summary"])
    run_id = str(summary["run_id"])
    for endpoint_report in cast(list[dict[str, object]], report["tribunal_reports"]):
        endpoint_report["run_id"] = run_id
        path = output_dir / f"tribunal-{endpoint_report['alias']}-{run_id}.json"
        write_report(endpoint_report, path)
    batch_path = output_dir / f"batch-{run_id}.json"
    write_report(summary, batch_path)


if __name__ == "__main__":
    raise SystemExit(main())
