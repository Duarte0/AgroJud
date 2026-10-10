"""Deterministic, in-memory source fixtures for demo and tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from hashlib import sha256

from agrojud.sources.contracts import (
    Cursor,
    JSONValue,
    SourceError,
    SourceErrorCode,
    SourcePage,
    SourceQuery,
    build_query_by_case_number,
    copy_json,
    parse_source_page,
    validate_fetch_arguments,
)


@dataclass(frozen=True, slots=True)
class SyntheticPageFixture:
    """One fixed group of raw hits and the total metadata to return for it."""

    hits: tuple[Mapping[str, JSONValue], ...]
    total_value: int | None = None
    total_relation: str | None = "eq"
    responded_at: datetime | None = None

    def __post_init__(self) -> None:
        copied_hits = tuple(copy_json(hit) for hit in self.hits)
        if not all(isinstance(hit, dict) for hit in copied_hits):
            raise SourceError(SourceErrorCode.VALIDATION, "Um hit da fixture não é um objeto JSON.")
        object.__setattr__(self, "hits", copied_hits)
        if self.total_value is not None and (
            isinstance(self.total_value, bool)
            or not isinstance(self.total_value, int)
            or self.total_value < 0
        ):
            raise SourceError(SourceErrorCode.VALIDATION, "O total da fixture é inválido.")
        if self.total_relation is not None and not self.total_relation.strip():
            raise SourceError(
                SourceErrorCode.VALIDATION, "A relação do total da fixture é inválida."
            )
        if self.responded_at is not None and (
            self.responded_at.tzinfo is None or self.responded_at.utcoffset() is None
        ):
            raise SourceError(
                SourceErrorCode.VALIDATION, "O horário da fixture precisa conter fuso."
            )


@dataclass(frozen=True, slots=True)
class SyntheticQueryFixture:
    """Pages and cursor-specific failures for one exact immutable query."""

    pages: tuple[SyntheticPageFixture, ...] = ()
    errors: tuple[tuple[Cursor | None, SourceError], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pages", tuple(self.pages))
        object.__setattr__(self, "errors", tuple(self.errors))
        if not all(isinstance(page, SyntheticPageFixture) for page in self.pages):
            raise SourceError(SourceErrorCode.VALIDATION, "As páginas da fixture são inválidas.")
        if not all(isinstance(error, SourceError) for _, error in self.errors):
            raise SourceError(SourceErrorCode.VALIDATION, "Os erros da fixture são inválidos.")


class SyntheticSourceAdapter:
    """Returns fixed fixtures; mutations take effect only when a fixture is replaced."""

    def __init__(
        self,
        fixtures: Mapping[SourceQuery, SyntheticQueryFixture] | None = None,
    ) -> None:
        self._fixtures = dict(fixtures or {})

    def replace_fixture(self, query: SourceQuery, fixture: SyntheticQueryFixture) -> None:
        """Replace one query's data between executions, simulating source mutation."""

        if not isinstance(query, SourceQuery) or not isinstance(fixture, SyntheticQueryFixture):
            raise SourceError(SourceErrorCode.VALIDATION, "A fixture sintética é inválida.")
        self._fixtures[query] = fixture

    def fetch_page(
        self,
        query: SourceQuery,
        cursor: Cursor | None,
        page_size: int,
    ) -> SourcePage:
        normalized_cursor = validate_fetch_arguments(query, cursor, page_size)
        fixture = self._fixtures.get(query)
        if fixture is None:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "Não há fixture sintética configurada para esta consulta.",
            )

        for error_cursor, error in fixture.errors:
            if error_cursor == normalized_cursor:
                raise SourceError(
                    error.code,
                    error.message,
                    status_code=error.status_code,
                    retry_after=error.retry_after,
                )

        page_index, hit_index = self._resolve_position(fixture.pages, normalized_cursor)
        if page_index >= len(fixture.pages):
            return parse_source_page(
                {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}},
                datetime.now(UTC),
            )

        fixture_page = fixture.pages[page_index]
        hits = fixture_page.hits[hit_index : hit_index + page_size]
        response_time = fixture_page.responded_at or datetime.now(UTC)
        total: dict[str, int | str] | None
        if fixture_page.total_value is None:
            total = None
        else:
            total = {"value": fixture_page.total_value}
            if fixture_page.total_relation is not None:
                total["relation"] = fixture_page.total_relation
        return parse_source_page(
            {"hits": {"total": total, "hits": list(hits)}},
            response_time,
        )

    def fetch_by_case_number(
        self,
        process_number: str,
        cursor: Cursor | None = None,
        page_size: int = 100,
    ) -> SourcePage:
        """Fetch one fixture page for an exact, locally validated CNJ number."""

        return self.fetch_page(build_query_by_case_number(process_number), cursor, page_size)

    @staticmethod
    def _resolve_position(
        pages: Sequence[SyntheticPageFixture],
        cursor: Cursor | None,
    ) -> tuple[int, int]:
        if cursor is None:
            return (0, 0)

        matches: list[tuple[int, int]] = []
        for page_index, page in enumerate(pages):
            for hit_index, hit in enumerate(page.hits):
                sort_values = hit.get("sort")
                if isinstance(sort_values, list) and tuple(sort_values) == cursor:
                    next_hit = hit_index + 1
                    if next_hit < len(page.hits):
                        matches.append((page_index, next_hit))
                    else:
                        matches.append((page_index + 1, 0))
        if len(matches) > 1:
            raise SourceError(
                SourceErrorCode.CONTRACT,
                "A fixture contém cursores repetidos e não permite avançar com segurança.",
            )
        if not matches:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O cursor não pertence à fixture sintética selecionada.",
            )
        return matches[0]


def build_demo_fixture(query: SourceQuery) -> SyntheticQueryFixture:
    """Create one fixed, explicitly synthetic hit for a local demo query."""

    if not isinstance(query, SourceQuery):
        raise SourceError(SourceErrorCode.VALIDATION, "A consulta da fixture é inválida.")
    process_number = query.process_number or "00000010020268090001"
    source_id = "demo-" + sha256(repr(query).encode("utf-8")).hexdigest()[:24]
    filed_date = query.filed_from or date(2025, 1, 15)
    filed_at = datetime.combine(filed_date, time(hour=9), tzinfo=UTC)
    updated_at = datetime.combine(filed_date, time(hour=10), tzinfo=UTC)
    class_code = query.class_codes[0] if query.class_codes else 1116
    subject_codes = query.subject_codes or (10501,)
    movement_code = query.movement_codes[0] if query.movement_codes else 26
    timestamp = updated_at.isoformat().replace("+00:00", "Z")
    source: dict[str, JSONValue] = {
        "id": source_id,
        "numeroProcesso": process_number,
        "tribunal": "TJGO",
        "dataAjuizamento": filed_at.strftime("%Y%m%d%H%M%S"),
        "@timestamp": timestamp,
        "grau": "G1",
        "classe": {"codigo": class_code, "nome": f"Classe TPU {class_code}"},
        "assuntos": [{"codigo": code, "nome": f"Assunto TPU {code}"} for code in subject_codes],
        "orgaoJulgador": {"codigo": query.court_unit_code or 1, "nome": "Unidade sintética TJGO"},
        "movimentos": [
            {
                "codigo": movement_code,
                "nome": f"Movimento TPU {movement_code}",
                "dataHora": timestamp,
            }
        ],
        "demonstracaoSintetica": True,
    }
    sort_values: list[JSONValue] = [
        timestamp if term.field_name == "@timestamp" else source_id for term in query.sort
    ]
    hit: dict[str, JSONValue] = {"_id": source_id, "_source": source, "sort": sort_values}
    return SyntheticQueryFixture(
        pages=(SyntheticPageFixture(hits=(hit,), total_value=1, total_relation="eq"),)
    )
