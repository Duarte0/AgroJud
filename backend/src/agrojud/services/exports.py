"""Consistent, bounded CSV exports of locally persisted processes."""

from __future__ import annotations

import csv
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from agrojud.api.process_filters import ProcessFilters, SignalEnvironment, process_predicates
from agrojud.db.models import (
    Collection,
    CollectionObservation,
    CollectionResult,
    Process,
    ProcessTriage,
    ProcessWatchlistEntry,
    Representation,
    RepresentationSubject,
)
from agrojud.services.triage import state_from_record

CSV_HEADERS = (
    "numero_cnj",
    "tribunal",
    "classes",
    "graus",
    "orgaos",
    "assuntos",
    "triagem",
    "vinculo_rural",
    "motivos_captura",
    "acompanhado",
    "primeira_observacao",
    "ultima_observacao",
    "data_source",
    "exportado_em",
)
_BATCH_SIZE = 500
_FORMULA_PREFIXES = frozenset("=+-@\t\r")


class ExportLimitExceeded(Exception):
    """The matching process set is larger than the configured synchronous budget."""

    def __init__(self, *, limit: int, process_count: int) -> None:
        self.limit = limit
        self.process_count = process_count
        super().__init__(
            f"A exportação contém {process_count} processos e excede o limite de {limit}. "
            "Restrinja os filtros e tente novamente."
        )


@dataclass(frozen=True, slots=True)
class ProcessCsvExport:
    path: Path
    filename: str
    exported_at: datetime


@dataclass(slots=True)
class _ProcessValues:
    tribunals: set[str] = field(default_factory=set)
    classes: set[str] = field(default_factory=set)
    degrees: set[str] = field(default_factory=set)
    court_units: set[str] = field(default_factory=set)
    subjects: set[str] = field(default_factory=set)
    capture_reasons: set[str] = field(default_factory=set)
    first_observed_at: datetime | None = None
    fallback_first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None


def create_process_csv_export(
    session_factory: sessionmaker[Session],
    filters: ProcessFilters,
    *,
    environment: SignalEnvironment,
    process_limit: int,
) -> ProcessCsvExport:
    """Write a complete CSV artifact before the API begins sending its response."""

    path: Path | None = None
    exported_at = datetime.now(UTC)
    try:
        with session_factory() as session, session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            predicates = process_predicates(filters, environment=environment)
            process_count = int(
                session.scalar(select(func.count()).select_from(Process).where(*predicates)) or 0
            )
            if process_count > process_limit:
                raise ExportLimitExceeded(limit=process_limit, process_count=process_count)

            temporary = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8-sig",
                newline="",
                prefix="agrojud-processes-",
                suffix=".csv",
                delete=False,
            )
            path = Path(temporary.name)
            with temporary:
                writer = csv.writer(
                    temporary,
                    delimiter=";",
                    quotechar='"',
                    quoting=csv.QUOTE_MINIMAL,
                    lineterminator="\r\n",
                )
                writer.writerow(CSV_HEADERS)
                _write_process_rows(
                    writer,
                    session,
                    predicates,
                    environment=environment,
                    exported_at=exported_at,
                )

        return ProcessCsvExport(
            path=path,
            filename=f"agrojud-processos-{environment}.csv",
            exported_at=exported_at,
        )
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise


def neutralize_spreadsheet_formula(value: str) -> str:
    """Prefix formula-like text with an apostrophe, even after leading whitespace."""

    first_non_whitespace = value.lstrip()
    if first_non_whitespace and first_non_whitespace[0] in _FORMULA_PREFIXES:
        return f"'{value}"
    return value


def _write_process_rows(
    writer: Any,
    session: Session,
    predicates: Sequence[ColumnElement[bool]],
    *,
    environment: SignalEnvironment,
    exported_at: datetime,
) -> None:
    for processes in _process_batches(session, predicates):
        values = _load_process_values(session, processes)
        triage_records = session.scalars(
            select(ProcessTriage).where(ProcessTriage.process_id.in_([p.id for p in processes]))
        ).all()
        triage_by_process = {record.process_id: record for record in triage_records}
        watched_process_ids = set(
            session.scalars(
                select(ProcessWatchlistEntry.process_id).where(
                    ProcessWatchlistEntry.process_id.in_([p.id for p in processes]),
                    ProcessWatchlistEntry.active.is_(True),
                )
            ).all()
        )

        for process in processes:
            data = values[process.id]
            triage = state_from_record(triage_by_process.get(process.id))
            row = (
                process.numero_cnj,
                _joined_values(data.tribunals),
                _joined_values(data.classes),
                _joined_values(data.degrees),
                _joined_values(data.court_units),
                _joined_values(data.subjects),
                _triage_decision_label(triage.decision),
                _rural_link_label(triage.rural_link),
                _joined_values(data.capture_reasons),
                "Sim" if process.id in watched_process_ids else "Não",
                _format_datetime(data.first_observed_at or data.fallback_first_observed_at),
                _format_datetime(data.last_observed_at),
                environment,
                _format_datetime(exported_at),
            )
            writer.writerow(
                tuple(
                    value if index == 0 else neutralize_spreadsheet_formula(value)
                    for index, value in enumerate(row)
                )
            )


def _process_batches(
    session: Session,
    predicates: Sequence[ColumnElement[bool]],
) -> Iterator[list[Process]]:
    last_number: str | None = None
    last_id: UUID | None = None
    while True:
        statement = select(Process).where(*predicates)
        if last_number is not None and last_id is not None:
            statement = statement.where(
                or_(
                    Process.numero_cnj > last_number,
                    and_(Process.numero_cnj == last_number, Process.id > last_id),
                )
            )
        processes = list(
            session.scalars(
                statement.order_by(Process.numero_cnj.asc(), Process.id.asc()).limit(_BATCH_SIZE)
            ).all()
        )
        if not processes:
            return
        yield processes
        last_number = processes[-1].numero_cnj
        last_id = processes[-1].id


def _load_process_values(
    session: Session, processes: Sequence[Process]
) -> dict[UUID, _ProcessValues]:
    process_ids = [process.id for process in processes]
    values = {process_id: _ProcessValues() for process_id in process_ids}
    representation_rows = (
        session.execute(
            select(
                Representation.id.label("representation_id"),
                Representation.process_id.label("process_id"),
                Representation.tribunal.label("tribunal"),
                Representation.class_code.label("class_code"),
                Representation.class_name.label("class_name"),
                Representation.grau.label("degree"),
                Representation.court_unit_code.label("court_unit_code"),
                Representation.court_unit_name.label("court_unit_name"),
                Representation.created_at.label("created_at"),
                Representation.last_observed_at.label("last_observed_at"),
            )
            .where(Representation.process_id.in_(process_ids))
            .order_by(Representation.process_id.asc(), Representation.id.asc())
        )
        .mappings()
        .all()
    )
    representation_ids: list[UUID] = []
    for row in representation_rows:
        representation_id = row["representation_id"]
        process_id = row["process_id"]
        representation_ids.append(representation_id)
        process_values = values[process_id]
        process_values.tribunals.add(row["tribunal"])
        _add_value(
            process_values.classes,
            _labeled_value(row["class_name"], row["class_code"]),
        )
        _add_value(process_values.degrees, row["degree"])
        _add_value(
            process_values.court_units,
            _labeled_value(row["court_unit_name"], row["court_unit_code"]),
        )
        created_at = row["created_at"]
        if (
            process_values.fallback_first_observed_at is None
            or created_at < process_values.fallback_first_observed_at
        ):
            process_values.fallback_first_observed_at = created_at
        last_observed_at = row["last_observed_at"]
        if last_observed_at is not None and (
            process_values.last_observed_at is None
            or last_observed_at > process_values.last_observed_at
        ):
            process_values.last_observed_at = last_observed_at

    if representation_ids:
        subject_rows = (
            session.execute(
                select(
                    Representation.process_id.label("process_id"),
                    RepresentationSubject.subject_code.label("subject_code"),
                    RepresentationSubject.subject_name.label("subject_name"),
                )
                .join(
                    RepresentationSubject,
                    RepresentationSubject.representation_id == Representation.id,
                )
                .where(Representation.id.in_(representation_ids))
            )
            .mappings()
            .all()
        )
        for row in subject_rows:
            process_id = row["process_id"]
            _add_value(
                values[process_id].subjects,
                _labeled_value(row["subject_name"], row["subject_code"]),
            )

        first_observations = (
            session.execute(
                select(
                    Representation.process_id.label("process_id"),
                    func.min(CollectionObservation.observed_at).label("observed_at"),
                )
                .join(
                    CollectionObservation,
                    CollectionObservation.representation_id == Representation.id,
                )
                .where(Representation.id.in_(representation_ids))
                .group_by(Representation.process_id)
            )
            .mappings()
            .all()
        )
        for row in first_observations:
            values[row["process_id"]].first_observed_at = row["observed_at"]

        collection_rows = (
            session.execute(
                select(
                    Representation.process_id.label("process_id"),
                    Collection.resolved_criteria.label("resolved_criteria"),
                )
                .join(CollectionResult, CollectionResult.representation_id == Representation.id)
                .join(Collection, Collection.id == CollectionResult.collection_id)
                .where(Representation.id.in_(representation_ids))
            )
            .mappings()
            .all()
        )
        for row in collection_rows:
            reason = _capture_explanation(row["resolved_criteria"])
            if reason is not None:
                values[row["process_id"]].capture_reasons.add(reason)

    return values


def _capture_explanation(criteria: object) -> str | None:
    if not isinstance(criteria, dict):
        return None
    query = criteria.get("query")
    if not isinstance(query, dict):
        return None
    catalog_snapshot = query.get("catalog_snapshot")
    if not isinstance(catalog_snapshot, dict):
        return None
    explanation = catalog_snapshot.get("capture_explanation")
    if isinstance(explanation, str) and explanation.strip():
        return explanation
    return None


def _labeled_value(name: str | None, code: str | None) -> str | None:
    if name and code:
        return f"{name} ({code})"
    return name or code


def _add_value(target: set[str], value: str | None) -> None:
    if value:
        target.add(value)


def _joined_values(values: set[str]) -> str:
    return "; ".join(sorted(values))


def _triage_decision_label(value: str) -> str:
    return {"pending": "Pendente", "relevant": "Relevante", "discarded": "Descartado"}[value]


def _rural_link_label(value: str) -> str:
    return {"unconfirmed": "Não confirmado", "confirmed": "Confirmado"}[value]


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    normalized = value.astimezone(UTC) if value.tzinfo is not None else value
    return normalized.isoformat().replace("+00:00", "Z")
