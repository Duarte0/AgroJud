"""Typed contracts shared by source adapters."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Literal, Protocol

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type Cursor = tuple[JSONScalar, ...]


class SourceErrorCode(StrEnum):
    """Stable categories for local and remote source failures."""

    VALIDATION = "VALIDATION"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK = "NETWORK"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    CONTRACT = "CONTRACT"


class SourceError(Exception):
    """Sanitized, typed error raised by a source adapter."""

    def __init__(
        self,
        code: SourceErrorCode,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
        field_path: str | None = None,
        validation_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after
        self.field_path = field_path
        self.validation_code = validation_code

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class SortTerm:
    """One allowlisted DataJud sort term."""

    field_name: str = "@timestamp"
    order: Literal["asc", "desc"] = "asc"

    def __post_init__(self) -> None:
        if self.field_name != "@timestamp" or self.order not in ("asc", "desc"):
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "A ordenação informada não é permitida nesta versão do contrato.",
            )


DEFAULT_SORT: tuple[SortTerm, ...] = (SortTerm(),)
_CNJ_DIGITS = re.compile(r"^\d{20}$")
_CNJ_FORMATTED = re.compile(r"^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$")


@dataclass(frozen=True, slots=True)
class SourceQuery:
    """Immutable, allowlisted query snapshot for the TJGO source."""

    tribunal: str = "TJGO"
    filed_from: date | None = None
    filed_to: date | None = None
    class_codes: tuple[int, ...] = ()
    subject_codes: tuple[int, ...] = ()
    court_unit_code: int | None = None
    preset_id: str | None = None
    preset_version: str | None = None
    process_number: str | None = None
    sort: tuple[SortTerm, ...] = DEFAULT_SORT

    def __post_init__(self) -> None:
        if self.tribunal != "TJGO":
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "Esta consulta aceita somente o tribunal TJGO.",
            )

        object.__setattr__(self, "class_codes", _normalize_codes(self.class_codes, "classe"))
        object.__setattr__(self, "subject_codes", _normalize_codes(self.subject_codes, "assunto"))

        if self.court_unit_code is not None:
            _validate_positive_code(self.court_unit_code, "órgão julgador")
        if (self.filed_from is None) != (self.filed_to is None):
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O intervalo de ajuizamento precisa informar início e fim.",
            )
        if self.filed_from is not None and self.filed_to is not None:
            if type(self.filed_from) is not date or type(self.filed_to) is not date:
                raise SourceError(
                    SourceErrorCode.VALIDATION,
                    "O intervalo de ajuizamento deve usar datas sem horário.",
                )
            if self.filed_from >= self.filed_to:
                raise SourceError(
                    SourceErrorCode.VALIDATION,
                    "O início do ajuizamento deve ser anterior ao fim exclusivo.",
                )

        if (self.preset_id is None) != (self.preset_version is None):
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "Identificador e versão do preset devem ser informados juntos.",
            )
        for name, value in (("preset", self.preset_id), ("versão do preset", self.preset_version)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise SourceError(SourceErrorCode.VALIDATION, f"O campo {name} é inválido.")

        if self.process_number is not None and (
            not isinstance(self.process_number, str)
            or not _CNJ_DIGITS.fullmatch(self.process_number)
        ):
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O número CNJ deve conter exatamente 20 dígitos sem formatação.",
            )

        if not self.sort or not all(isinstance(term, SortTerm) for term in self.sort):
            raise SourceError(
                SourceErrorCode.VALIDATION, "A consulta precisa de uma ordenação permitida."
            )
        object.__setattr__(self, "sort", tuple(self.sort))

        has_search_filter = any(
            (
                self.process_number is not None,
                self.filed_from is not None,
                bool(self.class_codes),
                bool(self.subject_codes),
                self.court_unit_code is not None,
            )
        )
        if not has_search_filter:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "Informe ao menos um filtro antes de consultar o TJGO.",
            )


@dataclass(frozen=True, slots=True)
class SourceHit:
    """Raw hit plus the source fields needed by later normalization."""

    source_id: JSONValue | None
    source: JSONValue | None
    sort_values: Cursor
    raw: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class SourcePage:
    """One successful source response, including successful empty responses."""

    hits: tuple[SourceHit, ...]
    total_value: int | None
    total_relation: str | None
    cursor_final: Cursor | None
    responded_at: datetime
    raw_envelope: Mapping[str, JSONValue] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.hits


@dataclass(frozen=True, slots=True)
class NormalizedSourceCover:
    """Minimal normalized identity while retaining every original source field."""

    source_id: str
    process_number: str
    tribunal: Literal["TJGO"]
    raw_source: Mapping[str, JSONValue]


class SourceAdapter(Protocol):
    """Synchronous, single-attempt source interface."""

    def fetch_page(
        self,
        query: SourceQuery,
        cursor: Cursor | None,
        page_size: int,
    ) -> SourcePage:
        """Fetch one page or raise a typed ``SourceError``."""

    def fetch_by_case_number(
        self,
        process_number: str,
        cursor: Cursor | None = None,
        page_size: int = 100,
    ) -> SourcePage:
        """Fetch one page for an exact, locally validated CNJ number."""


def build_query_by_case_number(value: str) -> SourceQuery:
    """Build an exact TJGO query from a formatted or unformatted CNJ number."""

    if not isinstance(value, str):
        raise SourceError(SourceErrorCode.VALIDATION, "O número CNJ deve ser texto.")
    if _CNJ_DIGITS.fullmatch(value):
        digits = value
    elif _CNJ_FORMATTED.fullmatch(value):
        digits = re.sub(r"\D", "", value)
    else:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O número CNJ deve ter 20 dígitos ou usar a formatação oficial.",
        )
    return SourceQuery(process_number=digits)


def validate_fetch_arguments(
    query: SourceQuery,
    cursor: Sequence[object] | None,
    page_size: int,
) -> Cursor | None:
    """Validate local inputs before an adapter performs any external work."""

    if not isinstance(query, SourceQuery):
        raise SourceError(SourceErrorCode.VALIDATION, "A consulta de fonte é inválida.")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O tamanho da página deve estar entre 1 e 100.",
        )
    if cursor is None:
        return None
    if isinstance(cursor, (str, bytes)) or not isinstance(cursor, Sequence) or not cursor:
        raise SourceError(SourceErrorCode.VALIDATION, "O cursor deve ser uma lista não vazia.")
    values: list[JSONScalar] = []
    for value in cursor:
        if isinstance(value, float) and not isfinite(value):
            raise SourceError(SourceErrorCode.VALIDATION, "O cursor contém um valor inválido.")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise SourceError(SourceErrorCode.VALIDATION, "O cursor contém um valor inválido.")
        values.append(value)
    return tuple(values)


def build_datajud_payload(
    query: SourceQuery,
    cursor: Cursor | None,
    page_size: int,
) -> dict[str, object]:
    """Compile the allowlisted query model into the DataJud request body."""

    normalized_cursor = validate_fetch_arguments(query, cursor, page_size)
    filters: list[dict[str, object]] = [{"match": {"tribunal": "TJGO"}}]

    if query.process_number is not None:
        filters.append({"match": {"numeroProcesso": query.process_number}})
    if query.filed_from is not None and query.filed_to is not None:
        filters.append(
            {
                "range": {
                    "dataAjuizamento": {
                        "gte": query.filed_from.isoformat(),
                        "lt": query.filed_to.isoformat(),
                    }
                }
            }
        )
    if query.class_codes:
        filters.append({"terms": {"classe.codigo": list(query.class_codes)}})
    if query.subject_codes:
        filters.append({"terms": {"assuntos.codigo": list(query.subject_codes)}})
    if query.court_unit_code is not None:
        filters.append({"match": {"orgaoJulgador.codigo": query.court_unit_code}})

    payload: dict[str, object] = {
        "size": page_size,
        "query": {"bool": {"filter": filters}},
        "sort": [{term.field_name: {"order": term.order}} for term in query.sort],
    }
    if normalized_cursor is not None:
        payload["search_after"] = list(normalized_cursor)
    return payload


def parse_source_page(payload: object, responded_at: datetime) -> SourcePage:
    """Validate the response envelope while retaining individual hits untouched."""

    if not isinstance(payload, Mapping):
        raise SourceError(SourceErrorCode.CONTRACT, "A resposta da fonte não é um objeto JSON.")
    raw_hits_container = payload.get("hits")
    if not isinstance(raw_hits_container, Mapping):
        raise SourceError(
            SourceErrorCode.CONTRACT, "O envelope da resposta não contém hits válidos."
        )
    raw_hits = raw_hits_container.get("hits")
    if not isinstance(raw_hits, list):
        raise SourceError(SourceErrorCode.CONTRACT, "A lista de hits da resposta é inválida.")

    hits: list[SourceHit] = []
    for raw_hit in raw_hits:
        if not isinstance(raw_hit, Mapping):
            raise SourceError(
                SourceErrorCode.CONTRACT, "Um item do envelope de hits não é um objeto."
            )
        raw_sort = raw_hit.get("sort")
        if not isinstance(raw_sort, list) or not raw_sort:
            raise SourceError(
                SourceErrorCode.CONTRACT, "Um hit não contém valores de ordenação válidos."
            )
        sort_values: list[JSONScalar] = []
        for value in raw_sort:
            if isinstance(value, float) and not isfinite(value):
                raise SourceError(
                    SourceErrorCode.CONTRACT, "A ordenação retornada contém valor inválido."
                )
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise SourceError(
                    SourceErrorCode.CONTRACT, "A ordenação retornada contém valor inválido."
                )
            sort_values.append(value)
        copied_raw = copy_json(raw_hit)
        if not isinstance(copied_raw, dict):  # pragma: no cover - guarded above
            raise SourceError(SourceErrorCode.CONTRACT, "Um hit da resposta é inválido.")
        raw_mapping: Mapping[str, JSONValue] = MappingProxyType(copied_raw)
        hits.append(
            SourceHit(
                source_id=copy_json(copied_raw.get("_id")),
                source=copy_json(copied_raw.get("_source")),
                sort_values=tuple(sort_values),
                raw=raw_mapping,
            )
        )

    total_value, total_relation = _parse_total(raw_hits_container.get("total"))
    copied_payload = copy_json(payload)
    if not isinstance(copied_payload, dict):  # pragma: no cover - guarded above
        raise SourceError(SourceErrorCode.CONTRACT, "O envelope da resposta é inválido.")
    raw_mapping = MappingProxyType(copied_payload)
    if responded_at.tzinfo is None or responded_at.utcoffset() is None:
        raise SourceError(SourceErrorCode.CONTRACT, "O horário de resposta precisa conter fuso.")

    return SourcePage(
        hits=tuple(hits),
        total_value=total_value,
        total_relation=total_relation,
        cursor_final=hits[-1].sort_values if hits else None,
        responded_at=responded_at,
        raw_envelope=raw_mapping,
    )


def normalize_source_cover(hit: SourceHit) -> NormalizedSourceCover:
    """Normalize only the fields required to identify a TJGO source cover."""

    source = hit.source
    if not isinstance(source, Mapping):
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O payload do hit não contém uma capa em objeto.",
            field_path="_source",
            validation_code="COVER_NOT_OBJECT",
        )

    source_id = source.get("id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "A capa não contém identificador de origem textual.",
            field_path="_source.id",
            validation_code="SOURCE_ID_INVALID",
        )
    if not isinstance(hit.source_id, str) or not hit.source_id.strip():
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O hit não contém identificador de origem textual.",
            field_path="_id",
            validation_code="HIT_SOURCE_ID_INVALID",
        )
    if source_id != hit.source_id:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "Os identificadores do hit e da capa divergem; a representação foi rejeitada.",
            field_path="_source.id",
            validation_code="SOURCE_ID_MISMATCH",
        )

    process_number = source.get("numeroProcesso")
    if not isinstance(process_number, str) or not _CNJ_DIGITS.fullmatch(process_number):
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "A capa não contém número CNJ com exatamente 20 dígitos.",
            field_path="_source.numeroProcesso",
            validation_code="CNJ_INVALID",
        )
    if source.get("tribunal") != "TJGO":
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "A capa não pertence ao tribunal TJGO.",
            field_path="_source.tribunal",
            validation_code="TRIBUNAL_INVALID",
        )

    raw_source = copy_json(dict(source))
    if not isinstance(raw_source, dict):  # pragma: no cover - source is already a mapping
        raise SourceError(SourceErrorCode.VALIDATION, "O payload da capa não é um objeto.")
    return NormalizedSourceCover(
        source_id=source_id,
        process_number=process_number,
        tribunal="TJGO",
        raw_source=MappingProxyType(raw_source),
    )


def copy_json(value: object) -> JSONValue:
    """Deep-copy JSON data without changing object or array shapes."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise SourceError(
                SourceErrorCode.CONTRACT, "A resposta JSON contém um número inválido."
            )
        return value
    if isinstance(value, Mapping):
        copied: dict[str, JSONValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise SourceError(
                    SourceErrorCode.CONTRACT, "A resposta JSON contém uma chave inválida."
                )
            copied[key] = copy_json(nested)
        return copied
    if isinstance(value, (list, tuple)):
        return [copy_json(nested) for nested in value]
    raise SourceError(SourceErrorCode.CONTRACT, "A resposta contém um valor fora do contrato JSON.")


def _normalize_codes(values: tuple[int, ...], label: str) -> tuple[int, ...]:
    if not isinstance(values, (tuple, list)):
        raise SourceError(SourceErrorCode.VALIDATION, f"A lista de códigos de {label} é inválida.")
    normalized: set[int] = set()
    for value in values:
        _validate_positive_code(value, label)
        normalized.add(value)
    return tuple(sorted(normalized))


def _validate_positive_code(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SourceError(
            SourceErrorCode.VALIDATION, f"O código de {label} deve ser inteiro positivo."
        )


def _parse_total(value: object) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        raise SourceError(SourceErrorCode.CONTRACT, "O total retornado pela fonte é inválido.")
    if isinstance(value, int):
        if value < 0:
            raise SourceError(SourceErrorCode.CONTRACT, "O total retornado pela fonte é inválido.")
        return value, None
    if not isinstance(value, Mapping):
        raise SourceError(
            SourceErrorCode.CONTRACT, "Os metadados de total da resposta são inválidos."
        )
    total_value = value.get("value")
    relation = value.get("relation")
    if total_value is not None and (
        isinstance(total_value, bool) or not isinstance(total_value, int) or total_value < 0
    ):
        raise SourceError(SourceErrorCode.CONTRACT, "O valor total da resposta é inválido.")
    if relation is not None and (not isinstance(relation, str) or not relation.strip()):
        raise SourceError(SourceErrorCode.CONTRACT, "A relação do total da resposta é inválida.")
    return total_value, relation
