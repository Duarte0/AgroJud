"""Versioned thematic presets and TPU evidence for the TJGO source."""

from __future__ import annotations

import calendar
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agrojud.sources.contracts import (
    JSONValue,
    SourceError,
    SourceErrorCode,
    SourceQuery,
    copy_json,
)

type EvidenceState = Literal["validated", "incompatible", "inconclusive"]
type EvidenceName = Literal["tpu_status", "query_status", "sample_status"]
type FilterDimension = Literal["class", "subject", "movement"]
type PresetEnvironment = Literal["demo", "real"]
type CatalogItemKind = Literal["preset", "candidate_signal"]

_EVIDENCE_NAMES: tuple[EvidenceName, ...] = (
    "tpu_status",
    "query_status",
    "sample_status",
)
_FILTER_KEYS: tuple[tuple[str, FilterDimension], ...] = (
    ("classes", "class"),
    ("subjects", "subject"),
    ("movements", "movement"),
)
_FILTER_DIMENSIONS: tuple[FilterDimension, ...] = ("class", "subject", "movement")


@dataclass(frozen=True, slots=True)
class Evidence:
    """One independently assessed evidence dimension."""

    state: EvidenceState
    reason: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TPUCodeEvidence:
    """One code checked against the current public CNJ SGT table."""

    dimension: FilterDimension
    code: int
    name: str
    parent_code: int | None
    situation: str
    tjgo_first_grade: bool
    tjgo_second_grade: bool
    search_term: str
    source_id: str


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """A preset or a candidate rule, with its version and evidence gates."""

    kind: CatalogItemKind
    id: str
    version: str
    family: str
    name: str
    purpose: str
    justification: str
    filters: Mapping[FilterDimension, tuple[int, ...]]
    include_descendants: bool
    rural_link_status: str
    capture_explanation: str
    evidence: Mapping[EvidenceName, Evidence]
    required_for_real: tuple[EvidenceName, ...]
    demo_available: bool
    runtime_implemented: bool


@dataclass(frozen=True, slots=True)
class CatalogFamily:
    """A SPEC family and whether its taxonomy investigation is recorded."""

    id: str
    name: str
    investigation_state: Literal["investigated", "pending"]
    item_ids: tuple[str, ...]
    pending_reason: str | None


@dataclass(frozen=True, slots=True)
class ThematicCatalog:
    """Validated immutable view of the versioned JSON catalog."""

    schema_version: int
    catalog_version: str
    consulted_at: date
    tpu_version: str
    tribunal: str
    evidence_report: str
    default_date_window: Mapping[str, JSONValue]
    items: tuple[CatalogItem, ...]
    families: tuple[CatalogFamily, ...]
    codes: Mapping[tuple[FilterDimension, int], TPUCodeEvidence]


@dataclass(frozen=True, slots=True)
class ItemAvailability:
    """Environment-specific availability and human-readable blocking reasons."""

    item_id: str
    environment: PresetEnvironment
    enabled: bool
    reasons: tuple[str, ...]
    label: str


@dataclass(frozen=True, slots=True)
class CompiledPreset:
    """Allowlisted query and immutable catalog/date context for one preset."""

    catalog_version: str
    tpu_version: str
    evidence_report: str
    environment: PresetEnvironment
    item: CatalogItem
    query: SourceQuery
    window_template: Mapping[str, JSONValue]
    resolved_from: date
    resolved_to_exclusive: date
    explanations: tuple[dict[str, JSONValue], ...]

    def snapshot(self) -> dict[str, JSONValue]:
        """Return the catalog justification frozen with a new collection."""

        evidence: dict[str, JSONValue] = {}
        for key in _EVIDENCE_NAMES:
            value = self.item.evidence[key]
            evidence[key] = {
                "state": value.state,
                "reason": value.reason,
                "evidence_ids": list(value.evidence_ids),
            }

        return {
            "snapshot_schema_version": 1,
            "catalog_version": self.catalog_version,
            "tpu_version": self.tpu_version,
            "evidence_report": self.evidence_report,
            "environment": self.environment,
            "preset_id": self.item.id,
            "preset_version": self.item.version,
            "family": self.item.family,
            "name": self.item.name,
            "justification": self.item.justification,
            "capture_explanation": self.item.capture_explanation,
            "rural_link_status": self.item.rural_link_status,
            "evidence": evidence,
            "required_for_real": list(self.item.required_for_real),
            "effective_codes": {
                "class_codes": list(self.query.class_codes),
                "subject_codes": list(self.query.subject_codes),
                "movement_codes": list(self.query.movement_codes),
            },
            "combinations": {"within_dimension": "OR", "between_dimensions": "AND"},
            "include_descendants": self.item.include_descendants,
            "inclusion_explanations": list(self.explanations),
            "window_template": copy_json(dict(self.window_template)),
            "resolved_interval": {
                "filed_from": self.resolved_from.isoformat(),
                "filed_to_exclusive": self.resolved_to_exclusive.isoformat(),
            },
        }


def validate_catalog_document(value: object) -> ThematicCatalog:
    """Parse and validate a catalog document, including evidence for each code."""

    document = _object(value, "catalog")
    if _integer(document.get("schema_version"), "schema_version") != 1:
        raise ValueError("Versão de schema do catálogo temático não suportada.")

    version = _text(document.get("catalog_version"), "catalog_version")
    consulted_at = _iso_date(document.get("consulted_at"), "consulted_at")
    tpu_version = _text(document.get("tpu_version"), "tpu_version")
    scope = _object(document.get("scope"), "scope")
    tribunal = _text(scope.get("tribunal"), "scope.tribunal")
    if tribunal != "TJGO":
        raise ValueError("O catálogo desta entrega precisa estar limitado ao TJGO.")

    window = _object(document.get("default_date_window"), "default_date_window")
    if (
        window.get("kind") != "relative_calendar_months"
        or _integer(window.get("months"), "default_date_window.months") != 12
    ):
        raise ValueError("A janela relativa default precisa ser de 12 meses de calendário.")
    try:
        ZoneInfo(_text(window.get("timezone"), "default_date_window.timezone"))
    except ZoneInfoNotFoundError:
        raise ValueError("O fuso da janela default não é reconhecido.") from None
    default_date_window = cast(dict[str, JSONValue], copy_json(window))

    evidence_report = _text(document.get("evidence_report"), "evidence_report")
    if not evidence_report.startswith("docs/evidence/") or ".." in evidence_report.split("/"):
        raise ValueError("evidence_report precisa ser um caminho local de evidência.")
    raw_sources = _array(document.get("sources"), "sources")
    source_ids: set[str] = set()
    for index, raw_source in enumerate(raw_sources):
        source = _object(raw_source, f"sources[{index}]")
        source_id = _text(source.get("id"), f"sources[{index}].id")
        source_url = _text(source.get("url"), f"sources[{index}].url")
        if not source_url.startswith("https://"):
            raise ValueError(f"sources[{index}].url precisa usar HTTPS.")
        if source_id in source_ids:
            raise ValueError(f"Fonte de catálogo duplicada: {source_id}.")
        source_ids.add(source_id)

    raw_codes = _array(document.get("tpu_code_evidence"), "tpu_code_evidence")
    code_evidence: dict[tuple[FilterDimension, int], TPUCodeEvidence] = {}
    for index, raw_code in enumerate(raw_codes):
        path = f"tpu_code_evidence[{index}]"
        entry = _object(raw_code, path)
        dimension = _dimension(entry.get("dimension"), f"{path}.dimension")
        code = _positive_integer(entry.get("code"), f"{path}.code")
        parent_value = entry.get("parent_code")
        parent_code = (
            None if parent_value is None else _positive_integer(parent_value, f"{path}.parent_code")
        )
        situation = _text(entry.get("situation"), f"{path}.situation")
        if situation not in ("A", "I"):
            raise ValueError(f"{path}.situation precisa ser A ou I.")
        evidence = TPUCodeEvidence(
            dimension=dimension,
            code=code,
            name=_text(entry.get("name"), f"{path}.name"),
            parent_code=parent_code,
            situation=situation,
            tjgo_first_grade=_boolean(entry.get("tjgo_first_grade"), f"{path}.tjgo_first_grade"),
            tjgo_second_grade=_boolean(entry.get("tjgo_second_grade"), f"{path}.tjgo_second_grade"),
            search_term=_text(entry.get("search_term"), f"{path}.search_term"),
            source_id=_text(entry.get("source_id"), f"{path}.source_id"),
        )
        if evidence.source_id not in source_ids:
            raise ValueError(f"{path}.source_id não referencia uma fonte cadastrada.")
        key = (dimension, code)
        if key in code_evidence:
            raise ValueError(f"Evidência TPU duplicada para {dimension}:{code}.")
        code_evidence[key] = evidence

    raw_items = _array(document.get("items"), "items")
    items: list[CatalogItem] = []
    items_by_id: dict[str, CatalogItem] = {}
    for index, raw_item in enumerate(raw_items):
        item = _parse_item(raw_item, index, code_evidence)
        if item.id in items_by_id:
            raise ValueError(f"Identificador de catálogo duplicado: {item.id}.")
        items.append(item)
        items_by_id[item.id] = item

    raw_families = _array(document.get("families"), "families")
    families: list[CatalogFamily] = []
    family_ids: set[str] = set()
    for index, raw_family in enumerate(raw_families):
        path = f"families[{index}]"
        family = _object(raw_family, path)
        family_id = _text(family.get("id"), f"{path}.id")
        if family_id in family_ids:
            raise ValueError(f"Família de catálogo duplicada: {family_id}.")
        family_ids.add(family_id)
        state = family.get("investigation_state")
        if state not in ("investigated", "pending"):
            raise ValueError(f"{path}.investigation_state inválido.")
        item_ids = tuple(
            _text(x, f"{path}.item_ids") for x in _array(family.get("item_ids"), f"{path}.item_ids")
        )
        pending_reason = _optional_text(family.get("pending_reason"), f"{path}.pending_reason")
        if state == "pending" and not pending_reason:
            raise ValueError(f"Família pendente sem motivo explícito: {family_id}.")
        if state == "investigated" and not item_ids:
            raise ValueError(f"Família investigada sem item nem motivo pendente: {family_id}.")
        for item_id in item_ids:
            if item_id not in items_by_id or items_by_id[item_id].family != family_id:
                raise ValueError(f"A família {family_id} referencia item ausente ou divergente.")
        families.append(
            CatalogFamily(
                id=family_id,
                name=_text(family.get("name"), f"{path}.name"),
                investigation_state=cast(Literal["investigated", "pending"], state),
                item_ids=item_ids,
                pending_reason=pending_reason,
            )
        )

    for item in items:
        if not any(item.id in family.item_ids for family in families):
            raise ValueError(f"Item sem família investigada ou pendente: {item.id}.")

    return ThematicCatalog(
        schema_version=1,
        catalog_version=version,
        consulted_at=consulted_at,
        tpu_version=tpu_version,
        tribunal=tribunal,
        evidence_report=evidence_report,
        default_date_window=default_date_window,
        items=tuple(items),
        families=tuple(families),
        codes=code_evidence,
    )


@lru_cache(maxsize=1)
def load_catalog() -> ThematicCatalog:
    """Load the checked-in catalog once per process."""

    raw = files("agrojud.sources").joinpath("thematic_catalog.v1.json").read_text(encoding="utf-8")
    return validate_catalog_document(json.loads(raw))


def list_presets() -> tuple[CatalogItem, ...]:
    """List versioned collection presets without implying real availability."""

    return tuple(item for item in load_catalog().items if item.kind == "preset")


def list_candidate_signals() -> tuple[CatalogItem, ...]:
    """List candidate signal rules; this SPEC does not execute them."""

    return tuple(item for item in load_catalog().items if item.kind == "candidate_signal")


def get_item_availability(
    item_id: str,
    environment: PresetEnvironment,
) -> ItemAvailability:
    """Return the evidence-derived environment gate for a catalog item."""

    if environment not in ("demo", "real"):
        raise SourceError(SourceErrorCode.VALIDATION, "O ambiente do catálogo é inválido.")
    item = _item_by_id(item_id)
    reasons: list[str] = []

    if environment == "demo":
        if item.kind != "preset" or not item.demo_available:
            reasons.append("Este item não é um preset disponível na demonstração sintética.")
        if not item.runtime_implemented:
            reasons.append("A execução deste item está fora do escopo implementado.")
        return ItemAvailability(
            item_id=item.id,
            environment=environment,
            enabled=not reasons,
            reasons=tuple(reasons)
            or ("Demonstração sintética explícita; isto não valida o TJGO.",),
            label="demonstrativo sintético",
        )

    for evidence_name in item.required_for_real:
        evidence = item.evidence[evidence_name]
        if evidence.state != "validated":
            reasons.append(f"{evidence_name}: {evidence.state} — {evidence.reason}")
    if not item.runtime_implemented:
        reasons.append("A execução deste item está fora do escopo implementado.")
    return ItemAvailability(
        item_id=item.id,
        environment=environment,
        enabled=not reasons,
        reasons=tuple(reasons),
        label="habilitado no real" if not reasons else "desabilitado no real",
    )


def compile_preset(
    preset_id: str,
    *,
    environment: PresetEnvironment,
    reference_time: datetime | None = None,
    filed_from: date | None = None,
    filed_through_inclusive: date | None = None,
) -> CompiledPreset:
    """Compile a known preset into allowlisted filters and a frozen date range."""

    item = _item_by_id(preset_id)
    if item.kind != "preset":
        raise SourceError(SourceErrorCode.VALIDATION, "O item informado não é um preset.")
    availability = get_item_availability(preset_id, environment)
    if not availability.enabled:
        detail = "; ".join(availability.reasons)
        raise SourceError(
            SourceErrorCode.VALIDATION,
            f"O preset {preset_id} está {availability.label}: {detail}",
            validation_code="PRESET_UNAVAILABLE",
        )

    resolved_from, resolved_to, template = _resolve_window(
        reference_time=reference_time,
        filed_from=filed_from,
        filed_through_inclusive=filed_through_inclusive,
    )
    catalog = load_catalog()
    explanations = _inclusion_explanations(item, catalog.codes)
    query = SourceQuery(
        filed_from=resolved_from,
        filed_to=resolved_to,
        class_codes=item.filters["class"],
        subject_codes=item.filters["subject"],
        movement_codes=item.filters["movement"],
        preset_id=item.id,
        preset_version=item.version,
    )
    return CompiledPreset(
        catalog_version=catalog.catalog_version,
        tpu_version=catalog.tpu_version,
        evidence_report=catalog.evidence_report,
        environment=environment,
        item=item,
        query=query,
        window_template=template,
        resolved_from=resolved_from,
        resolved_to_exclusive=resolved_to,
        explanations=explanations,
    )


def validate_catalog_snapshot(
    value: object,
    *,
    environment: PresetEnvironment | None = None,
    query: SourceQuery | None = None,
) -> dict[str, JSONValue]:
    """Validate a stored catalog snapshot without consulting today's catalog."""

    try:
        return _validate_catalog_snapshot(value, environment=environment, query=query)
    except SourceError:
        raise
    except ValueError:
        raise SourceError(
            SourceErrorCode.CONTRACT,
            "O snapshot do catálogo contém campos ou valores inválidos.",
        ) from None


def _validate_catalog_snapshot(
    value: object,
    *,
    environment: PresetEnvironment | None,
    query: SourceQuery | None,
) -> dict[str, JSONValue]:

    snapshot = _object(value, "catalog_snapshot")
    if _integer(snapshot.get("snapshot_schema_version"), "snapshot_schema_version") != 1:
        raise SourceError(SourceErrorCode.CONTRACT, "A versão do snapshot do catálogo é inválida.")
    for key in (
        "catalog_version",
        "tpu_version",
        "evidence_report",
        "preset_id",
        "preset_version",
        "family",
        "name",
        "justification",
        "capture_explanation",
        "rural_link_status",
    ):
        if not isinstance(snapshot.get(key), str) or not str(snapshot[key]).strip():
            raise SourceError(SourceErrorCode.CONTRACT, f"O snapshot do catálogo não contém {key}.")
    if snapshot.get("environment") not in ("demo", "real"):
        raise SourceError(
            SourceErrorCode.CONTRACT, "O ambiente do snapshot do catálogo é inválido."
        )
    if environment is not None and snapshot.get("environment") != environment:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O ambiente do preset compilado não corresponde ao ambiente da coleta.",
        )
    if snapshot.get("include_descendants") is not False:
        raise SourceError(
            SourceErrorCode.CONTRACT,
            "O snapshot não pode expandir descendentes sem evidência TPU explícita.",
        )
    combinations = _object(snapshot.get("combinations"), "catalog_snapshot.combinations")
    if combinations != {"within_dimension": "OR", "between_dimensions": "AND"}:
        raise SourceError(
            SourceErrorCode.CONTRACT,
            "As combinações OR/AND do snapshot do catálogo são inválidas.",
        )
    _object(snapshot.get("window_template"), "catalog_snapshot.window_template")
    explanations = _array(
        snapshot.get("inclusion_explanations"), "catalog_snapshot.inclusion_explanations"
    )
    for index, raw_explanation in enumerate(explanations):
        explanation = _object(raw_explanation, f"inclusion_explanations[{index}]")
        if explanation.get("dimension") not in ("class", "subject", "movement"):
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "O snapshot contém dimensão de explicação fora da allowlist.",
            )
        _positive_integer(explanation.get("code"), f"inclusion_explanations[{index}].code")
        _text(explanation.get("name"), f"inclusion_explanations[{index}].name")
    evidence = _object(snapshot.get("evidence"), "catalog_snapshot.evidence")
    if set(evidence) != set(_EVIDENCE_NAMES):
        raise SourceError(
            SourceErrorCode.CONTRACT,
            "O snapshot não preserva as três dimensões de evidência.",
        )
    for evidence_name in _EVIDENCE_NAMES:
        state = _object(evidence[evidence_name], f"catalog_snapshot.evidence.{evidence_name}")
        if state.get("state") not in ("validated", "incompatible", "inconclusive"):
            raise SourceError(
                SourceErrorCode.CONTRACT,
                f"O snapshot contém estado inválido em {evidence_name}.",
            )
        _text(state.get("reason"), f"catalog_snapshot.evidence.{evidence_name}.reason")

    codes = _object(snapshot.get("effective_codes"), "catalog_snapshot.effective_codes")
    expected_keys = {"class_codes", "subject_codes", "movement_codes"}
    if set(codes) != expected_keys:
        raise SourceError(
            SourceErrorCode.CONTRACT, "Os códigos efetivos do snapshot são inválidos."
        )
    normalized_codes: dict[str, tuple[int, ...]] = {}
    for key in expected_keys:
        values = _array(codes[key], f"catalog_snapshot.effective_codes.{key}")
        normalized_codes[key] = tuple(_positive_integer(code, key) for code in values)

    interval = _object(snapshot.get("resolved_interval"), "catalog_snapshot.resolved_interval")
    filed_from = _iso_date(interval.get("filed_from"), "resolved_interval.filed_from")
    filed_to = _iso_date(interval.get("filed_to_exclusive"), "resolved_interval.filed_to_exclusive")
    if filed_from >= filed_to:
        raise SourceError(SourceErrorCode.CONTRACT, "O intervalo salvo no catálogo é inválido.")

    if query is not None:
        if (
            snapshot.get("preset_id") != query.preset_id
            or snapshot.get("preset_version") != query.preset_version
        ):
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O preset e a versão não correspondem ao snapshot que seria salvo.",
            )
        if (
            normalized_codes
            != {
                "class_codes": query.class_codes,
                "subject_codes": query.subject_codes,
                "movement_codes": query.movement_codes,
            }
            or filed_from != query.filed_from
            or filed_to != query.filed_to
        ):
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "Os filtros efetivos não correspondem ao snapshot do catálogo.",
            )

    copied = copy_json(snapshot)
    if not isinstance(copied, dict):  # pragma: no cover - _object has already checked this
        raise SourceError(SourceErrorCode.CONTRACT, "O snapshot do catálogo precisa ser objeto.")
    return copied


def _parse_item(
    value: object,
    index: int,
    code_evidence: Mapping[tuple[FilterDimension, int], TPUCodeEvidence],
) -> CatalogItem:
    path = f"items[{index}]"
    raw = _object(value, path)
    kind = raw.get("kind")
    if kind not in ("preset", "candidate_signal"):
        raise ValueError(f"{path}.kind inválido.")
    filters_raw = _object(raw.get("filters"), f"{path}.filters")
    filters: dict[FilterDimension, tuple[int, ...]] = {}
    for filter_key, dimension in _FILTER_KEYS:
        values = _array(filters_raw.get(filter_key), f"{path}.filters.{filter_key}")
        codes = tuple(
            sorted({_positive_integer(code, f"{path}.filters.{filter_key}") for code in values})
        )
        filters[dimension] = codes
        for code in codes:
            code_record = code_evidence.get((dimension, code))
            if code_record is None:
                raise ValueError(f"{path} usa código sem evidência TPU: {dimension}:{code}.")
            if code_record.situation != "A" or not (
                code_record.tjgo_first_grade or code_record.tjgo_second_grade
            ):
                raise ValueError(
                    f"{path} usa código inativo ou inaplicável ao TJGO: {dimension}:{code}."
                )
    if set(filters_raw) != {key for key, _ in _FILTER_KEYS}:
        raise ValueError(f"{path}.filters contém dimensão não permitida.")

    include_descendants = _boolean(raw.get("include_descendants"), f"{path}.include_descendants")
    if include_descendants:
        raise ValueError(
            f"{path} tenta expandir descendentes sem lista explícita e evidência TPU individual."
        )
    combinations = _object(raw.get("combinations"), f"{path}.combinations")
    if combinations != {"within_dimension": "OR", "between_dimensions": "AND"}:
        raise ValueError(f"{path} altera a combinação OR/AND estabelecida pela SPEC-010.")

    evidence_raw = _object(raw.get("tpu_status"), f"{path}.tpu_status")
    evidence_by_name: dict[EvidenceName, Evidence] = {
        "tpu_status": _parse_evidence(evidence_raw, "tpu_status", path),
        "query_status": _parse_evidence(
            _object(raw.get("query_status"), f"{path}.query_status"), "query_status", path
        ),
        "sample_status": _parse_evidence(
            _object(raw.get("sample_status"), f"{path}.sample_status"), "sample_status", path
        ),
    }
    tpu_codes = [code for codes in filters.values() for code in codes]
    if not tpu_codes and evidence_by_name["tpu_status"].state == "validated":
        raise ValueError(f"{path} não pode validar TPU sem códigos pesquisados.")
    expected_evidence_ids = {
        f"{dimension}:{code}" for dimension, codes in filters.items() for code in codes
    }
    if (
        evidence_by_name["tpu_status"].state == "validated"
        and set(evidence_by_name["tpu_status"].evidence_ids) != expected_evidence_ids
    ):
        raise ValueError(f"{path}.tpu_status não referencia exatamente os códigos TPU usados.")

    required_raw = _array(raw.get("required_for_real"), f"{path}.required_for_real")
    required: list[EvidenceName] = []
    for name in required_raw:
        if name not in _EVIDENCE_NAMES or name in required:
            raise ValueError(f"{path}.required_for_real contém dimensão inválida ou repetida.")
        required.append(cast(EvidenceName, name))
    if "tpu_status" not in required:
        raise ValueError(f"{path} precisa exigir evidência TPU para habilitação real.")

    return CatalogItem(
        kind=cast(CatalogItemKind, kind),
        id=_text(raw.get("id"), f"{path}.id"),
        version=_text(raw.get("version"), f"{path}.version"),
        family=_text(raw.get("family"), f"{path}.family"),
        name=_text(raw.get("name"), f"{path}.name"),
        purpose=_text(raw.get("purpose"), f"{path}.purpose"),
        justification=_text(raw.get("justification"), f"{path}.justification"),
        filters=filters,
        include_descendants=include_descendants,
        rural_link_status=_text(raw.get("rural_link_status"), f"{path}.rural_link_status"),
        capture_explanation=_text(raw.get("capture_explanation"), f"{path}.capture_explanation"),
        evidence=evidence_by_name,
        required_for_real=tuple(required),
        demo_available=_boolean(raw.get("demo_available"), f"{path}.demo_available"),
        runtime_implemented=_boolean(raw.get("runtime_implemented"), f"{path}.runtime_implemented"),
    )


def _parse_evidence(
    value: Mapping[str, Any],
    name: EvidenceName,
    path: str,
) -> Evidence:
    state = value.get("state")
    if state not in ("validated", "incompatible", "inconclusive"):
        raise ValueError(f"{path}.{name}.state inválido.")
    reason = _text(value.get("reason"), f"{path}.{name}.reason")
    ids_value = value.get("evidence_ids", [])
    ids = tuple(
        _text(item, f"{path}.{name}.evidence_ids")
        for item in _array(ids_value, f"{path}.{name}.evidence_ids")
    )
    return Evidence(state=cast(EvidenceState, state), reason=reason, evidence_ids=ids)


def _inclusion_explanations(
    item: CatalogItem,
    codes: Mapping[tuple[FilterDimension, int], TPUCodeEvidence],
) -> tuple[dict[str, JSONValue], ...]:
    explanations: list[dict[str, JSONValue]] = []
    for dimension in _FILTER_DIMENSIONS:
        for code in item.filters[dimension]:
            evidence = codes[(dimension, code)]
            explanations.append(
                {
                    "dimension": dimension,
                    "code": code,
                    "name": evidence.name,
                    "condition": "OR within this filter; AND with other non-empty dimensions",
                }
            )
    return tuple(explanations)


def _resolve_window(
    *,
    reference_time: datetime | None,
    filed_from: date | None,
    filed_through_inclusive: date | None,
) -> tuple[date, date, dict[str, JSONValue]]:
    if (filed_from is None) != (filed_through_inclusive is None):
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "Informe início e fim inclusivo do ajuizamento juntos.",
        )
    if filed_from is not None and filed_through_inclusive is not None:
        if type(filed_from) is not date or type(filed_through_inclusive) is not date:
            raise SourceError(
                SourceErrorCode.VALIDATION, "As datas do preset devem ser datas locais."
            )
        try:
            filed_to = filed_through_inclusive + timedelta(days=1)
        except OverflowError:
            raise SourceError(
                SourceErrorCode.VALIDATION, "A data final do preset é inválida."
            ) from None
        if filed_from >= filed_to:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "A data inicial precisa ser anterior ao fim inclusivo.",
            )
        return (
            filed_from,
            filed_to,
            {
                "kind": "explicit_inclusive_dates",
                "filed_from": filed_from.isoformat(),
                "filed_through_inclusive": filed_through_inclusive.isoformat(),
                "timezone": "America/Sao_Paulo",
            },
        )

    window = load_catalog().default_date_window
    timezone_name = cast(str, window["timezone"])
    timezone = ZoneInfo(timezone_name)
    if reference_time is None:
        local_execution_date = datetime.now(timezone).date()
    else:
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O instante de execução precisa conter fuso horário.",
            )
        local_execution_date = reference_time.astimezone(timezone).date()
    months = cast(int, window["months"])
    try:
        start = _subtract_calendar_months(local_execution_date, months)
    except OverflowError, ValueError:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "A data de execução não permite resolver a janela relativa do preset.",
        ) from None
    if start >= local_execution_date:
        raise SourceError(SourceErrorCode.VALIDATION, "A janela relativa do preset é inválida.")
    return start, local_execution_date, cast(dict[str, JSONValue], copy_json(dict(window)))


def _subtract_calendar_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, month_zero_based = divmod(month_index, 12)
    month = month_zero_based + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _item_by_id(item_id: str) -> CatalogItem:
    if not isinstance(item_id, str) or not item_id.strip():
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O identificador do catálogo é inválido.",
            field_path="preset_id",
        )
    for item in load_catalog().items:
        if item.id == item_id:
            return item
    raise SourceError(
        SourceErrorCode.VALIDATION,
        "O preset ou regra informado não existe no catálogo.",
        field_path="preset_id",
    )


def _dimension(value: object, path: str) -> FilterDimension:
    if value not in ("class", "subject", "movement"):
        raise ValueError(f"{path} contém dimensão TPU não permitida.")
    return value


def _object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} precisa ser objeto JSON.")
    return value


def _array(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} precisa ser array JSON.")
    return value


def _text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path} precisa ser texto não vazio.")
    return value


def _optional_text(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _text(value, path)


def _integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} precisa ser inteiro.")
    return value


def _positive_integer(value: object, path: str) -> int:
    integer = _integer(value, path)
    if integer <= 0:
        raise ValueError(f"{path} precisa ser inteiro positivo.")
    return integer


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} precisa ser booleano.")
    return value


def _iso_date(value: object, path: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{path} precisa estar no formato ISO de data.")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{path} precisa estar no formato ISO de data.") from None
