"""Versioned user-defined search criteria backed by the curated TPU catalog."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agrojud.db.models import CustomSearch, CustomSearchVersion
from agrojud.sources.catalog import (
    CompiledPreset,
    compile_custom_search,
    compile_preset,
    list_official_themes,
)
from agrojud.sources.contracts import SourceError, SourceErrorCode

type SearchMode = Literal["demo", "real"]
type DimensionCombination = Literal["AND", "OR"]


class CustomSearchNotFoundError(LookupError):
    """A custom search or one of its revisions does not exist."""


class CustomSearchArchivedError(ValueError):
    """An archived custom search cannot be edited or selected for a new collection."""


class CustomSearchService:
    """Create immutable revisions and keep a mutable current-version/archive pointer."""

    def list_searches(
        self, session: Session, *, include_archived: bool = False
    ) -> list[CustomSearch]:
        statement = select(CustomSearch).order_by(CustomSearch.created_at.desc(), CustomSearch.id)
        if not include_archived:
            statement = statement.where(CustomSearch.archived_at.is_(None))
        return list(session.scalars(statement).all())

    def version(
        self,
        session: Session,
        search_id: UUID,
        version: int | None = None,
        *,
        include_archived: bool = True,
    ) -> CustomSearchVersion:
        search = session.get(CustomSearch, search_id)
        if search is None or (search.archived_at is not None and not include_archived):
            raise CustomSearchNotFoundError("A busca personalizada não existe.")
        selected_version = version or search.current_version
        revision = session.scalar(
            select(CustomSearchVersion).where(
                CustomSearchVersion.custom_search_id == search_id,
                CustomSearchVersion.version == selected_version,
            )
        )
        if revision is None:
            raise CustomSearchNotFoundError("A versão da busca personalizada não existe.")
        return revision

    def create(
        self,
        session: Session,
        *,
        name: str,
        description: str | None,
        class_codes: list[int],
        subject_codes: list[int],
        dimension_combination: DimensionCombination,
        mode: SearchMode,
        copy_from_theme: str | None = None,
        criteria_provided: bool = False,
    ) -> tuple[CustomSearch, CustomSearchVersion]:
        if copy_from_theme is not None:
            theme = next(
                (item for item in list_official_themes() if item.id == copy_from_theme), None
            )
            if theme is None:
                raise SourceError(
                    SourceErrorCode.VALIDATION,
                    "A cópia precisa partir de um tema oficial disponível.",
                    validation_code="OFFICIAL_THEME_NOT_FOUND",
                )
            if name.strip() == "":
                name = theme.name
            if description is None:
                description = theme.purpose
            if not criteria_provided:
                class_codes = list(theme.filters["class"])
                subject_codes = list(theme.filters["subject"])
                dimension_combination = theme.dimension_combination

        search_id = uuid4()
        normalized = _criteria(
            name=name,
            description=description,
            class_codes=class_codes,
            subject_codes=subject_codes,
            dimension_combination=dimension_combination,
        )
        _validate_compilable(search_id, 1, normalized, mode)
        now = _database_now(session)
        search = CustomSearch(
            id=search_id,
            current_version=1,
            archived_at=None,
            created_at=now,
            updated_at=now,
        )
        revision = _version_row(search_id, 1, normalized, now)
        session.add_all([search, revision])
        session.flush()
        return search, revision

    def patch(
        self,
        session: Session,
        search_id: UUID,
        *,
        name: str | None,
        description: str | None,
        description_provided: bool = False,
        class_codes: list[int] | None,
        subject_codes: list[int] | None,
        dimension_combination: DimensionCombination | None,
        mode: SearchMode,
    ) -> tuple[CustomSearch, CustomSearchVersion]:
        search = session.scalar(
            select(CustomSearch).where(CustomSearch.id == search_id).with_for_update()
        )
        if search is None:
            raise CustomSearchNotFoundError("A busca personalizada não existe.")
        if search.archived_at is not None:
            raise CustomSearchArchivedError("Uma busca arquivada não pode ser alterada.")
        current = self.version(session, search_id, search.current_version)
        normalized = _criteria(
            name=name if name is not None else current.name,
            description=(description if description_provided else current.description),
            class_codes=class_codes if class_codes is not None else current.class_codes,
            subject_codes=subject_codes if subject_codes is not None else current.subject_codes,
            dimension_combination=dimension_combination
            or cast(DimensionCombination, current.dimension_combination),
        )
        if _same_definition(current, normalized):
            return search, current
        next_version = search.current_version + 1
        _validate_compilable(search_id, next_version, normalized, mode)
        now = _database_now(session)
        revision = _version_row(search_id, next_version, normalized, now)
        session.add(revision)
        search.current_version = next_version
        search.updated_at = now
        session.flush()
        return search, revision

    def archive(self, session: Session, search_id: UUID) -> CustomSearch:
        search = session.scalar(
            select(CustomSearch).where(CustomSearch.id == search_id).with_for_update()
        )
        if search is None:
            raise CustomSearchNotFoundError("A busca personalizada não existe.")
        if search.archived_at is None:
            now = _database_now(session)
            search.archived_at = now
            search.updated_at = now
            session.flush()
        return search

    def compile(
        self,
        session: Session,
        search_id: UUID,
        version: int,
        *,
        mode: SearchMode,
        tribunal: str = "TJGO",
        reference_time: datetime | None = None,
        filed_from: date | None = None,
        filed_through_inclusive: date | None = None,
        require_active: bool = True,
    ) -> CompiledPreset:
        revision = self.version(session, search_id, version, include_archived=not require_active)
        if require_active:
            search = session.get(CustomSearch, search_id)
            if search is None:
                raise CustomSearchNotFoundError("A busca personalizada não existe.")
            if search.archived_at is not None:
                raise CustomSearchArchivedError("A busca personalizada está arquivada.")
        return compile_custom_search(
            custom_search_id=str(search_id),
            version=revision.version,
            name=revision.name,
            description=revision.description,
            class_codes=revision.class_codes,
            subject_codes=revision.subject_codes,
            dimension_combination=cast(DimensionCombination, revision.dimension_combination),
            environment=mode,
            tribunal=tribunal,
            reference_time=reference_time,
            filed_from=filed_from,
            filed_through_inclusive=filed_through_inclusive,
        )

    def compile_reference(
        self,
        session: Session,
        *,
        kind: Literal["official_theme", "custom_search"],
        definition_id: str,
        version: str | int,
        mode: SearchMode,
        tribunal: str = "TJGO",
        reference_time: datetime | None = None,
        filed_from: date | None = None,
        filed_through_inclusive: date | None = None,
    ) -> CompiledPreset:
        if kind == "official_theme":
            theme = next(
                (item for item in list_official_themes() if item.id == definition_id), None
            )
            if theme is None:
                raise SourceError(
                    SourceErrorCode.VALIDATION,
                    "O tema oficial não está disponível para novas buscas.",
                    validation_code="OFFICIAL_THEME_NOT_FOUND",
                )
            if not isinstance(version, str) or version != theme.version:
                raise SourceError(
                    SourceErrorCode.VALIDATION,
                    "A versão do tema oficial mudou; recarregue o catálogo antes de buscar.",
                    validation_code="SEARCH_DEFINITION_VERSION_CHANGED",
                )
            return compile_preset(
                theme.id,
                environment=mode,
                tribunal=tribunal,
                reference_time=reference_time,
                filed_from=filed_from,
                filed_through_inclusive=filed_through_inclusive,
            )
        if isinstance(version, bool) or not isinstance(version, int):
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "A versão da busca personalizada precisa ser inteira.",
                validation_code="SEARCH_DEFINITION_VERSION_INVALID",
            )
        try:
            search_id = UUID(definition_id)
        except ValueError:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "O identificador da busca personalizada é inválido.",
            ) from None
        try:
            search = session.get(CustomSearch, search_id)
            if search is None or search.archived_at is not None:
                raise CustomSearchNotFoundError("A busca personalizada não existe.")
            if version != search.current_version:
                raise SourceError(
                    SourceErrorCode.VALIDATION,
                    "Selecione a versão atual da busca personalizada.",
                    validation_code="CUSTOM_SEARCH_VERSION_CHANGED",
                )
            return self.compile(
                session,
                search_id,
                version,
                mode=mode,
                tribunal=tribunal,
                reference_time=reference_time,
                filed_from=filed_from,
                filed_through_inclusive=filed_through_inclusive,
            )
        except CustomSearchNotFoundError, CustomSearchArchivedError:
            raise SourceError(
                SourceErrorCode.VALIDATION,
                "A busca personalizada foi arquivada ou a versão não existe.",
                validation_code="CUSTOM_SEARCH_NOT_AVAILABLE",
            ) from None


def _criteria(
    *,
    name: str,
    description: str | None,
    class_codes: list[int],
    subject_codes: list[int],
    dimension_combination: DimensionCombination,
) -> dict[str, Any]:
    normalized_name = name.strip()
    normalized_description = description.strip() if description is not None else None
    if not normalized_name or len(normalized_name) > 160:
        raise SourceError(
            SourceErrorCode.VALIDATION, "O nome da busca deve ter de 1 a 160 caracteres."
        )
    if normalized_description is not None and len(normalized_description) > 2000:
        raise SourceError(
            SourceErrorCode.VALIDATION, "A descrição deve ter no máximo 2.000 caracteres."
        )
    if dimension_combination not in ("AND", "OR"):
        raise SourceError(
            SourceErrorCode.VALIDATION, "A combinação entre dimensões deve ser AND ou OR."
        )
    if not isinstance(class_codes, list) or not isinstance(subject_codes, list):
        raise SourceError(
            SourceErrorCode.VALIDATION, "Classes e assuntos devem ser listas de códigos."
        )
    return {
        "name": normalized_name,
        "description": normalized_description or None,
        "class_codes": _codes(class_codes, "class"),
        "subject_codes": _codes(subject_codes, "subject"),
        "dimension_combination": dimension_combination,
    }


def _codes(values: list[int], dimension: str) -> list[int]:
    if len(values) > 100 or any(
        isinstance(code, bool) or not isinstance(code, int) or code < 1 for code in values
    ):
        raise SourceError(SourceErrorCode.VALIDATION, f"A lista de códigos {dimension} é inválida.")
    return sorted(set(values))


def _validate_compilable(
    search_id: UUID,
    version: int,
    definition: dict[str, Any],
    mode: SearchMode,
) -> None:
    compile_custom_search(
        custom_search_id=str(search_id),
        version=version,
        name=definition["name"],
        description=definition["description"],
        class_codes=definition["class_codes"],
        subject_codes=definition["subject_codes"],
        dimension_combination=cast(DimensionCombination, definition["dimension_combination"]),
        environment=mode,
    )


def _version_row(
    search_id: UUID,
    version: int,
    definition: dict[str, Any],
    now: datetime,
) -> CustomSearchVersion:
    return CustomSearchVersion(
        id=uuid4(),
        custom_search_id=search_id,
        version=version,
        name=definition["name"],
        description=definition["description"],
        class_codes=definition["class_codes"],
        subject_codes=definition["subject_codes"],
        dimension_combination=definition["dimension_combination"],
        created_at=now,
    )


def _same_definition(current: CustomSearchVersion, definition: dict[str, Any]) -> bool:
    return bool(
        current.name == definition["name"]
        and current.description == definition["description"]
        and current.class_codes == definition["class_codes"]
        and current.subject_codes == definition["subject_codes"]
        and current.dimension_combination == definition["dimension_combination"]
    )


def _database_now(session: Session) -> datetime:
    return cast(datetime, session.scalar(select(func.now())))
