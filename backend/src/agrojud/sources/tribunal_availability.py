"""Fail-closed runtime gate over the explicitly promoted SPEC-024 review matrix."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agrojud.config import Settings
from agrojud.sources.catalog import list_presets, load_catalog
from agrojud.sources.contracts import SourceError, SourceErrorCode
from agrojud.sources.tribunals import ALLOWLIST_VERSION, TRIBUNAL_ALLOWLIST

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_S1_CAPABILITIES = frozenset(
    {
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
    }
)
_VALID_STATES = frozenset(
    {
        "VALIDATED",
        "INCONCLUSIVE",
        "INCOMPATIBLE",
        "NOT_RUN",
        "NOT_APPLICABLE",
        "OBSOLETE",
    }
)


@dataclass(frozen=True, slots=True)
class TribunalAvailability:
    tribunal: str
    s1_s2_state: str
    preset_state: str | None
    enabled: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class TribunalAvailabilitySnapshot:
    matrix_sha256: str | None
    allowlist_version: str | None
    catalog_version: str | None
    tpu_version: str | None
    selection_enabled: bool
    global_reasons: tuple[str, ...]
    tribunals: tuple[TribunalAvailability, ...]


@dataclass(frozen=True, slots=True)
class _MatrixRead:
    rows: Mapping[str, Mapping[str, Any]]
    matrix_sha256: str | None
    allowlist_version: str | None
    catalog_version: str | None
    tpu_version: str | None
    preset_versions: Mapping[str, str]
    reasons: tuple[str, ...]


def tribunal_availability(
    settings: Settings,
    *,
    preset_id: str | None = None,
) -> TribunalAvailabilitySnapshot:
    """Return every allowlisted target with a sanitized evidence and rollout reason."""

    current_presets = {item.id: item.version for item in list_presets()}
    if preset_id is not None and preset_id not in current_presets:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O preset informado não existe no catálogo atual.",
            field_path="preset_id",
            validation_code="PRESET_UNKNOWN",
        )
    matrix = _read_matrix(settings, current_presets)
    global_reasons = list(matrix.reasons)
    if not settings.datajud_multitribunal_enabled:
        global_reasons.append(
            "A seleção multitribunal está desativada pela configuração de rollout."
        )

    selection_enabled = settings.datajud_multitribunal_enabled and not matrix.reasons
    available: list[TribunalAvailability] = []
    for endpoint in TRIBUNAL_ALLOWLIST.values():
        row = matrix.rows.get(endpoint.alias)
        s1_state = _row_s1_state(row)
        preset_state = (
            _row_preset_state(
                row,
                preset_id,
                expected_catalog_version=load_catalog().catalog_version,
                expected_preset_version=current_presets[preset_id],
                expected_tpu_version=load_catalog().tpu_version,
                matrix_preset_version=matrix.preset_versions.get(preset_id),
            )
            if preset_id is not None
            else None
        )
        reasons: list[str] = []
        if matrix.reasons:
            reasons.extend(matrix.reasons)
        elif s1_state != "VALIDATED":
            reasons.append(f"S1/S2: {s1_state} para {endpoint.canonical_tribunal}.")
        if preset_id is not None and preset_state != "VALIDATED":
            reasons.append(
                f"S5: {preset_state or 'NOT_RUN'} para {endpoint.canonical_tribunal}/{preset_id}."
            )
        if not settings.datajud_multitribunal_enabled:
            reasons.append("A seleção multitribunal está desativada pela configuração de rollout.")
        enabled = (
            selection_enabled
            and s1_state == "VALIDATED"
            and (preset_id is None or preset_state == "VALIDATED")
        )
        available.append(
            TribunalAvailability(
                tribunal=endpoint.canonical_tribunal,
                s1_s2_state=s1_state,
                preset_state=preset_state,
                enabled=enabled,
                reason=" ".join(dict.fromkeys(reasons)) or None,
            )
        )
    return TribunalAvailabilitySnapshot(
        matrix_sha256=matrix.matrix_sha256,
        allowlist_version=matrix.allowlist_version,
        catalog_version=matrix.catalog_version,
        tpu_version=matrix.tpu_version,
        selection_enabled=selection_enabled,
        global_reasons=tuple(dict.fromkeys(global_reasons)),
        tribunals=tuple(available),
    )


def require_tribunal_available(
    settings: Settings,
    tribunal: str,
    *,
    preset_id: str | None = None,
) -> TribunalAvailability:
    """Raise before external work unless the exact tribunal capability is enabled."""

    snapshot = tribunal_availability(settings, preset_id=preset_id)
    normalized = tribunal.strip().upper()
    match = next((item for item in snapshot.tribunals if item.tribunal == normalized), None)
    if match is None:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O tribunal informado está fora da allowlist versionada.",
            field_path="tribunal",
            validation_code="TRIBUNAL_NOT_ALLOWLISTED",
        )
    if not match.enabled:
        raise SourceError(
            SourceErrorCode.CONTRACT,
            match.reason or "A combinação de tribunal e capacidade não está validada.",
            field_path="tribunal",
            validation_code="TRIBUNAL_CAPABILITY_UNAVAILABLE",
        )
    return match


def _read_matrix(settings: Settings, current_presets: Mapping[str, str]) -> _MatrixRead:
    rows: dict[str, Mapping[str, Any]] = {}
    reasons: list[str] = []
    path_value = settings.datajud_multitribunal_matrix_path
    expected_hash = settings.datajud_multitribunal_matrix_sha256
    if not path_value:
        reasons.append("A matriz revisada não foi promovida para a configuração do runtime.")
        return _MatrixRead(rows, None, None, None, None, {}, tuple(reasons))
    if not expected_hash or not _SHA256.fullmatch(expected_hash):
        reasons.append("O SHA-256 esperado da matriz está ausente ou inválido.")
        return _MatrixRead(rows, None, None, None, None, {}, tuple(reasons))
    try:
        content = Path(path_value).read_bytes()
        matrix_hash = hashlib.sha256(content).hexdigest()
    except OSError:
        reasons.append("A matriz revisada configurada não está acessível.")
        return _MatrixRead(rows, None, None, None, None, {}, tuple(reasons))
    if matrix_hash != expected_hash:
        reasons.append("O SHA-256 da matriz diverge do valor promovido na configuração.")
        return _MatrixRead(rows, matrix_hash, None, None, None, {}, tuple(reasons))
    try:
        value = json.loads(content)
    except UnicodeDecodeError, json.JSONDecodeError:
        reasons.append("A matriz revisada não contém JSON válido.")
        return _MatrixRead(rows, matrix_hash, None, None, None, {}, tuple(reasons))
    if not isinstance(value, Mapping):
        reasons.append("A matriz revisada precisa ser um objeto JSON.")
        return _MatrixRead(rows, matrix_hash, None, None, None, {}, tuple(reasons))

    review = value.get("matrix_review")
    if (
        not isinstance(review, Mapping)
        or review.get("state") != "REVIEWED"
        or review.get("decision") != "APPROVED"
        or not isinstance(review.get("matrix_sha256"), str)
        or not _SHA256.fullmatch(str(review.get("matrix_sha256")))
        or value.get("coverage_status") not in {"APPROVED_PARTIAL", "NATIONAL_COVERAGE_VALIDATED"}
    ):
        reasons.append("A matriz não tem revisão humana aprovada e íntegra.")

    allowlist_version = _text(value.get("allowlist_version"))
    catalog_version = _text(value.get("catalog_version"))
    tpu_version = _text(value.get("tpu_version"))
    preset_versions_raw = value.get("preset_versions")
    preset_versions = (
        {str(key): str(item) for key, item in preset_versions_raw.items()}
        if isinstance(preset_versions_raw, Mapping)
        else {}
    )
    catalog = load_catalog()
    if allowlist_version != ALLOWLIST_VERSION:
        reasons.append("A versão da allowlist diverge da matriz revisada.")
    if catalog_version != catalog.catalog_version:
        reasons.append("A versão do catálogo diverge da matriz revisada.")
    if tpu_version != catalog.tpu_version:
        reasons.append("A versão TPU diverge da matriz revisada.")
    if set(preset_versions) != set(current_presets):
        reasons.append("A lista de presets da matriz diverge do catálogo atual.")

    raw_rows = value.get("coverage_matrix")
    if not isinstance(raw_rows, list) or len(raw_rows) != len(TRIBUNAL_ALLOWLIST):
        reasons.append("A matriz não cobre exatamente os tribunais da allowlist.")
    else:
        expected_aliases = set(TRIBUNAL_ALLOWLIST)
        for raw_row in raw_rows:
            if not isinstance(raw_row, Mapping):
                reasons.append("A matriz contém uma linha de tribunal inválida.")
                continue
            alias = raw_row.get("alias")
            if not isinstance(alias, str) or alias not in expected_aliases or alias in rows:
                reasons.append("A matriz contém alias desconhecido ou duplicado.")
                continue
            endpoint = TRIBUNAL_ALLOWLIST[alias]
            if raw_row.get("canonical_tribunal") != endpoint.canonical_tribunal:
                reasons.append("A sigla canônica diverge da allowlist.")
                continue
            rows[alias] = raw_row
        if set(rows) != expected_aliases:
            reasons.append("A matriz não contém todos os aliases allowlisted.")
    for alias, row in rows.items():
        if _row_s1_state(row) not in _VALID_STATES:
            reasons.append(f"Estado S1/S2 inválido na matriz para {alias.upper()}.")
    return _MatrixRead(
        rows,
        matrix_hash,
        allowlist_version,
        catalog_version,
        tpu_version,
        preset_versions,
        tuple(dict.fromkeys(reasons)),
    )


def _row_s1_state(row: Mapping[str, Any] | None) -> str:
    if row is None:
        return "NOT_RUN"
    capabilities = row.get("s1_s2_capabilities")
    if not isinstance(capabilities, Mapping) or set(capabilities) != _S1_CAPABILITIES:
        return "INCONCLUSIVE"
    states: list[str] = []
    for value in capabilities.values():
        if not isinstance(value, Mapping):
            return "INCONCLUSIVE"
        state = value.get("state")
        if not isinstance(state, str) or state not in _VALID_STATES:
            return "INCONCLUSIVE"
        states.append(state)
    outer_state = row.get("s1_s2_state")
    if outer_state != "VALIDATED" or any(state != "VALIDATED" for state in states):
        if isinstance(outer_state, str) and outer_state in _VALID_STATES:
            return outer_state
        return "INCONCLUSIVE"
    return "VALIDATED"


def _row_preset_state(
    row: Mapping[str, Any] | None,
    preset_id: str,
    *,
    expected_catalog_version: str,
    expected_preset_version: str,
    expected_tpu_version: str,
    matrix_preset_version: str | None,
) -> str:
    if row is None:
        return "NOT_RUN"
    presets = row.get("s5_pairs")
    if not isinstance(presets, Mapping):
        return "INCONCLUSIVE"
    pair = presets.get(preset_id)
    if not isinstance(pair, Mapping):
        return "NOT_RUN"
    if matrix_preset_version != expected_preset_version:
        return "OBSOLETE"
    if (
        pair.get("current_catalog_version") != expected_catalog_version
        or pair.get("current_preset_version") != expected_preset_version
        or pair.get("current_tpu_version") != expected_tpu_version
    ):
        return "OBSOLETE"
    state = pair.get("pair_state")
    if not isinstance(state, str) or state not in _VALID_STATES:
        return "INCONCLUSIVE"
    return state


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
