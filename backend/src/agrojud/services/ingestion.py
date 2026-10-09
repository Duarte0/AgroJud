"""Transactional persistence of a source page without performing HTTP requests."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy.orm import Session

from agrojud.db.models import (
    Collection,
    CollectionObservation,
    Process,
    QuarantineRejection,
    Representation,
    RepresentationVersion,
)
from agrojud.db.movement_repositories import QuarantineRepository
from agrojud.db.repositories import (
    CollectionRepository,
    ObservationRepository,
    ProcessRepository,
    RepresentationRepository,
    VersionRepository,
)
from agrojud.domain.canonical_json import JSONValue, canonical_json_bytes, sha256_json
from agrojud.domain.persistence import (
    CollectionNotFoundError,
    ObservationPositionConflict,
    PayloadHashCollisionError,
    SourceIdentityConflict,
)
from agrojud.services.movement_ingestion import persist_movement_snapshot
from agrojud.services.news import register_watched_snapshot
from agrojud.sources.contracts import (
    SourceError,
    SourceErrorCode,
    SourceHit,
    SourcePage,
    copy_json,
    normalize_source_cover,
)

NORMALIZER_VERSION = "cover-normalizer-v1"
_COMPACT_TIMESTAMP = re.compile(r"^\d{14}$")

type CaptureOutcome = Literal["new", "updated", "unchanged"]
type SourceKey = Literal["datajud", "synthetic"]


@dataclass(frozen=True, slots=True)
class IngestionCounts:
    """Distinct representations in this page classified by their first hit."""

    new: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass(frozen=True, slots=True)
class IngestedVersion:
    representation_id: UUID
    version_id: UUID
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class SourceDateRegression:
    representation_id: UUID
    previous_version_id: UUID
    observed_version_id: UUID
    previous_source_date_original: str
    observed_source_date_original: str


@dataclass(frozen=True, slots=True)
class IngestPageResult:
    counts: IngestionCounts
    valid_hit_count: int
    rejected_hit_count: int
    representation_ids: tuple[UUID, ...]
    observation_ids: tuple[UUID, ...]
    versions: tuple[IngestedVersion, ...]
    date_regressions: tuple[SourceDateRegression, ...]
    quarantine_ids: tuple[UUID, ...]
    incomplete_representation_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class _RecordIssue:
    error_path: str
    validation_code: str
    normalizer_version: str


@dataclass(frozen=True, slots=True)
class _PreparedHit:
    source_id: str
    numero_cnj: str
    tribunal: str
    raw_payload: dict[str, JSONValue]
    payload_sha256: str
    class_code: str | None
    class_name: str | None
    grau: str | None
    court_unit_code: str | None
    court_unit_name: str | None
    source_filed_at_original: str | None
    source_filed_at: datetime | None
    source_filed_at_timezone_ambiguous: bool
    source_updated_at_original: str | None
    source_updated_at: datetime | None
    source_updated_at_timezone_ambiguous: bool
    subjects: tuple[tuple[str | None, str | None], ...]
    raw_hit: dict[str, JSONValue]


@dataclass(frozen=True, slots=True)
class _RejectedHit:
    raw_hit: dict[str, JSONValue]
    issue: _RecordIssue


def create_collection(
    session: Session,
    *,
    mode: Literal["demo", "real"],
    resolved_criteria: dict[str, JSONValue],
    previous_collection_id: UUID | None = None,
    created_at: datetime | None = None,
) -> Collection:
    """Persist a new collection identity in the caller's transaction."""

    return CollectionRepository(session).create(
        mode=mode,
        resolved_criteria=resolved_criteria,
        previous_collection_id=previous_collection_id,
        created_at=created_at,
    )


def ingest_page(
    session: Session,
    *,
    collection_id: UUID,
    page_key: str,
    source: SourceKey,
    page: SourcePage,
) -> IngestPageResult:
    """Persist one page atomically without committing the caller's transaction.

    Hit positions are one-based and stable within ``page_key``. Each page is
    protected by a savepoint so any identity, collision, or database failure
    rolls its effects back while leaving transaction ownership with the caller.
    """

    if not session.in_transaction():
        raise RuntimeError("A ingestão exige uma transação aberta pelo chamador.")
    if not isinstance(page_key, str) or not page_key.strip():
        raise ValueError("A chave da página deve ser texto não vazio.")
    if source not in ("datajud", "synthetic"):
        raise ValueError("A fonte de origem deve ser datajud ou synthetic.")
    if page.responded_at.tzinfo is None or page.responded_at.utcoffset() is None:
        raise ValueError("O horário local de observação precisa conter fuso.")

    prepared_hits = tuple(_prepare_hit(hit) for hit in page.hits)
    observed_at = page.responded_at.astimezone(UTC)
    collection_repository = CollectionRepository(session)
    observation_repository = ObservationRepository(session)
    process_repository = ProcessRepository(session)
    representation_repository = RepresentationRepository(session)
    version_repository = VersionRepository(session)
    quarantine_repository = QuarantineRepository(session)

    observation_ids: list[UUID] = []
    representation_ids: list[UUID] = []
    quarantine_ids: list[UUID] = []
    incomplete_representation_ids: list[UUID] = []
    version_by_id: dict[UUID, IngestedVersion] = {}
    first_outcome_by_representation: dict[UUID, CaptureOutcome] = {}
    regressions: list[SourceDateRegression] = []
    valid_hit_count = 0
    rejected_hit_count = 0

    with session.begin_nested():
        collection = collection_repository.lock(collection_id)
        if collection is None:
            raise CollectionNotFoundError("A coleta informada não existe.")
        expected_source: SourceKey = "synthetic" if collection.mode == "demo" else "datajud"
        if source != expected_source:
            raise ValueError("A fonte não corresponde ao modo registrado na coleta.")
        existing_ordinals = collection_repository.page_ordinals(collection_id, page_key)
        expected_ordinals = tuple(range(1, len(prepared_hits) + 1))
        if existing_ordinals and existing_ordinals != expected_ordinals:
            raise ObservationPositionConflict(
                "A página foi reapresentada com quantidade de hits diferente."
            )

        for hit_ordinal, prepared in enumerate(prepared_hits, start=1):
            existing = observation_repository.by_position(collection_id, page_key, hit_ordinal)
            existing_rejections = quarantine_repository.by_position(
                collection_id, page_key, hit_ordinal
            )
            raw_hit = prepared.raw_hit

            if existing_rejections:
                _assert_quarantine_replay_matches(existing_rejections, raw_hit)
            if existing is None and existing_rejections:
                rejected_hit_count += 1
                quarantine_ids.extend(item.id for item in existing_rejections)
                continue
            if isinstance(prepared, _RejectedHit):
                rejected_hit_count += 1
                if existing is not None:
                    raise ObservationPositionConflict(
                        "A posição já persistida como observação não pode virar rejeição."
                    )
                rejection = quarantine_repository.create_or_get(
                    collection_id=collection_id,
                    page_key=page_key,
                    hit_ordinal=hit_ordinal,
                    error_path=prepared.issue.error_path,
                    raw_hit=prepared.raw_hit,
                    validation_code=prepared.issue.validation_code,
                    normalizer_version=prepared.issue.normalizer_version,
                    observed_at=observed_at,
                )
                quarantine_ids.append(rejection.id)
                continue

            valid_hit_count += 1

            outcome: CaptureOutcome
            if existing is not None:
                observation, representation, process, version = existing
                _assert_replay_matches(
                    observation=observation,
                    representation=representation,
                    process=process,
                    version=version,
                    hit=prepared,
                    source=source,
                )
                representation_id = representation.id
                observation_id = observation.id
                version_id = version.id
                outcome = cast(CaptureOutcome, observation.capture_outcome)
                if observation.source_filed_at_regressed:
                    previous_version_id = observation.regressed_from_version_id
                    if previous_version_id is None:  # pragma: no cover - guarded by service writes
                        raise RuntimeError("A regressão não aponta para a versão anterior.")
                    previous = session.get(RepresentationVersion, previous_version_id)
                    if previous is None:  # pragma: no cover - guarded by the composite foreign key
                        raise RuntimeError("A versão anterior da regressão não pôde ser lida.")
                    if (
                        previous.source_filed_at_original is not None
                        and version.source_filed_at_original is not None
                    ):
                        regressions.append(
                            SourceDateRegression(
                                representation_id=representation_id,
                                previous_version_id=previous.id,
                                observed_version_id=version_id,
                                previous_source_date_original=previous.source_filed_at_original,
                                observed_source_date_original=version.source_filed_at_original,
                            )
                        )
            else:
                process, _process_created = process_repository.get_or_create(prepared.numero_cnj)
                representation, representation_created = representation_repository.get_or_create(
                    process_id=process.id,
                    source=source,
                    tribunal=prepared.tribunal,
                    source_id=prepared.source_id,
                )
                if representation.process_id != process.id:
                    raise SourceIdentityConflict(
                        "A mesma identidade de origem já está vinculada a outro número CNJ."
                    )

                previous_version_id = representation.latest_version_id
                previous_version = (
                    session.get(RepresentationVersion, previous_version_id)
                    if previous_version_id is not None
                    else None
                )
                version, _version_created = version_repository.get_or_create(
                    representation_id=representation.id,
                    payload_sha256=prepared.payload_sha256,
                    raw_payload=prepared.raw_payload,
                    normalizer_version=NORMALIZER_VERSION,
                    source_filed_at_original=prepared.source_filed_at_original,
                    source_filed_at=prepared.source_filed_at,
                    source_filed_at_timezone_ambiguous=(
                        prepared.source_filed_at_timezone_ambiguous
                    ),
                    source_updated_at_original=prepared.source_updated_at_original,
                    source_updated_at=prepared.source_updated_at,
                    source_updated_at_timezone_ambiguous=(
                        prepared.source_updated_at_timezone_ambiguous
                    ),
                    observed_at=observed_at,
                )
                if representation_created:
                    outcome = "new"
                elif (
                    previous_version is None
                    or previous_version.payload_sha256 != version.payload_sha256
                ):
                    outcome = "updated"
                else:
                    outcome = "unchanged"

                regressed = previous_version is not None and _filed_date_regressed(
                    previous_version.source_filed_at_original,
                    version.source_filed_at_original,
                )
                observation = observation_repository.create(
                    collection_id=collection_id,
                    representation_id=representation.id,
                    page_key=page_key,
                    hit_ordinal=hit_ordinal,
                    version_id=version.id,
                    observed_at=observed_at,
                    capture_outcome=outcome,
                    source_filed_at_regressed=regressed,
                    regressed_from_version_id=(
                        previous_version.id if regressed and previous_version is not None else None
                    ),
                )
                observation_id = observation.id
                representation_id = representation.id
                version_id = version.id

                observation_repository.ensure_collection_result(
                    collection_id=collection_id,
                    representation_id=representation.id,
                    first_observation_id=observation.id,
                    included_at=observed_at,
                    capture_outcome=outcome,
                )

                if representation.latest_version_id != version.id:
                    representation_repository.update_latest_projection(
                        representation,
                        version=version,
                        class_code=prepared.class_code,
                        class_name=prepared.class_name,
                        grau=prepared.grau,
                        court_unit_code=prepared.court_unit_code,
                        court_unit_name=prepared.court_unit_name,
                        source_filed_at_original=prepared.source_filed_at_original,
                        source_filed_at=prepared.source_filed_at,
                        source_filed_at_timezone_ambiguous=(
                            prepared.source_filed_at_timezone_ambiguous
                        ),
                        source_updated_at_original=prepared.source_updated_at_original,
                        source_updated_at=prepared.source_updated_at,
                        source_updated_at_timezone_ambiguous=(
                            prepared.source_updated_at_timezone_ambiguous
                        ),
                        observed_at=observed_at,
                        subjects=prepared.subjects,
                    )
                else:
                    representation.last_observed_at = observed_at
                    session.flush()

                if regressed and previous_version is not None:
                    regressions.append(
                        SourceDateRegression(
                            representation_id=representation.id,
                            previous_version_id=previous_version.id,
                            observed_version_id=version.id,
                            previous_source_date_original=(
                                previous_version.source_filed_at_original or ""
                            ),
                            observed_source_date_original=(version.source_filed_at_original or ""),
                        )
                    )

            movement_snapshot = persist_movement_snapshot(
                session,
                representation_id=representation.id,
                version_id=version.id,
                raw_source=prepared.raw_payload,
                observed_at=observed_at,
            )
            register_watched_snapshot(
                session,
                process_id=process.id,
                representation=representation,
                version=version,
                snapshot=movement_snapshot.snapshot,
                provenance="ingestion",
                first_observed_at=version.first_observed_at,
            )
            for issue in movement_snapshot.issues:
                rejection = quarantine_repository.create_or_get(
                    collection_id=collection_id,
                    page_key=page_key,
                    hit_ordinal=hit_ordinal,
                    error_path=issue.error_path,
                    raw_hit=raw_hit,
                    validation_code=issue.validation_code,
                    normalizer_version=movement_snapshot.snapshot.normalizer_version,
                    observed_at=observed_at,
                )
                quarantine_ids.append(rejection.id)
            if not movement_snapshot.snapshot.is_complete:
                if representation.id not in incomplete_representation_ids:
                    incomplete_representation_ids.append(representation.id)

            observation_ids.append(observation_id)
            if representation_id not in first_outcome_by_representation:
                representation_ids.append(representation_id)
                first_outcome_by_representation[representation_id] = outcome
            version_by_id.setdefault(
                version_id,
                IngestedVersion(
                    representation_id=representation_id,
                    version_id=version_id,
                    payload_sha256=version.payload_sha256,
                ),
            )

    counts = IngestionCounts(
        new=sum(outcome == "new" for outcome in first_outcome_by_representation.values()),
        updated=sum(outcome == "updated" for outcome in first_outcome_by_representation.values()),
        unchanged=sum(
            outcome == "unchanged" for outcome in first_outcome_by_representation.values()
        ),
    )
    return IngestPageResult(
        counts=counts,
        valid_hit_count=valid_hit_count,
        rejected_hit_count=rejected_hit_count,
        representation_ids=tuple(representation_ids),
        observation_ids=tuple(observation_ids),
        versions=tuple(version_by_id.values()),
        date_regressions=tuple(regressions),
        quarantine_ids=tuple(dict.fromkeys(quarantine_ids)),
        incomplete_representation_ids=tuple(incomplete_representation_ids),
    )


def _prepare_hit(hit: SourceHit) -> _PreparedHit | _RejectedHit:
    raw_hit = _serialize_source_hit(hit)
    try:
        cover = normalize_source_cover(hit)
    except SourceError as error:
        if error.code != SourceErrorCode.VALIDATION:
            raise
        if error.field_path is None or error.validation_code is None:
            raise RuntimeError(
                "A validação da capa não informou caminho e código estáveis."
            ) from error
        return _RejectedHit(
            raw_hit=raw_hit,
            issue=_RecordIssue(
                error_path=error.field_path,
                validation_code=error.validation_code,
                normalizer_version=NORMALIZER_VERSION,
            ),
        )

    copied_payload = copy_json(dict(cover.raw_source))
    if not isinstance(copied_payload, dict):  # pragma: no cover - normalized source is an object
        raise ValueError("O payload bruto da capa precisa ser um objeto JSON.")
    source = copied_payload
    class_fields = _object_field(source, "classe")
    court_fields = _object_field(source, "orgaoJulgador")
    subjects_value = source.get("assuntos")
    subjects: list[tuple[str | None, str | None]] = []
    if isinstance(subjects_value, list):
        for subject in subjects_value:
            if isinstance(subject, Mapping):
                code = _structured_code(subject.get("codigo"))
                name = _structured_text(subject.get("nome"))
                if code is not None or name is not None:
                    subjects.append((code, name))

    filed_original = _original_date(source.get("dataAjuizamento"))
    updated_original = _original_date(source.get("@timestamp"))
    filed_parsed = _parse_aware_datetime(filed_original)
    updated_parsed = _parse_aware_datetime(updated_original)
    return _PreparedHit(
        source_id=cover.source_id,
        numero_cnj=cover.process_number,
        tribunal=cover.tribunal,
        raw_payload=copied_payload,
        payload_sha256=sha256_json(copied_payload),
        class_code=_structured_code(class_fields.get("codigo")),
        class_name=_structured_text(class_fields.get("nome")),
        grau=_structured_text(source.get("grau")),
        court_unit_code=_structured_code(court_fields.get("codigo")),
        court_unit_name=_structured_text(court_fields.get("nome")),
        source_filed_at_original=filed_original,
        source_filed_at=filed_parsed,
        source_filed_at_timezone_ambiguous=_has_ambiguous_timezone(filed_original),
        source_updated_at_original=updated_original,
        source_updated_at=updated_parsed,
        source_updated_at_timezone_ambiguous=_has_ambiguous_timezone(updated_original),
        subjects=tuple(subjects),
        raw_hit=raw_hit,
    )


def _serialize_source_hit(hit: SourceHit) -> dict[str, JSONValue]:
    raw_hit = {
        "source_id": copy_json(hit.source_id),
        "source": copy_json(hit.source),
        "sort_values": copy_json(list(hit.sort_values)),
        "raw": copy_json(dict(hit.raw)),
    }
    return raw_hit


def _assert_quarantine_replay_matches(
    rejections: Sequence[QuarantineRejection], raw_hit: dict[str, JSONValue]
) -> None:
    expected = canonical_json_bytes(raw_hit)
    for rejection in rejections:
        if canonical_json_bytes(cast(JSONValue, rejection.raw_hit)) != expected:
            raise ObservationPositionConflict(
                "A posição da página já foi rejeitada com outro conteúdo bruto."
            )


def _object_field(source: Mapping[str, JSONValue], name: str) -> Mapping[str, JSONValue]:
    value = source.get(name)
    return value if isinstance(value, Mapping) else {}


def _structured_code(value: JSONValue | None) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _structured_text(value: JSONValue | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _original_date(value: JSONValue | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return canonical_json_bytes(value).decode("utf-8")


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        if _COMPACT_TIMESTAMP.fullmatch(value):
            return datetime.strptime(value, "%Y%m%d%H%M%S")
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_aware_datetime(value: str | None) -> datetime | None:
    parsed = _parse_datetime(value)
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _has_ambiguous_timezone(value: str | None) -> bool:
    parsed = _parse_datetime(value)
    return parsed is not None and (parsed.tzinfo is None or parsed.utcoffset() is None)


def _filed_date_regressed(previous: str | None, current: str | None) -> bool:
    previous_value = _parse_datetime(previous)
    current_value = _parse_datetime(current)
    if previous_value is None or current_value is None:
        return False
    previous_aware = previous_value.tzinfo is not None and previous_value.utcoffset() is not None
    current_aware = current_value.tzinfo is not None and current_value.utcoffset() is not None
    if previous_aware != current_aware:
        return False
    if previous_aware:
        previous_value = previous_value.astimezone(UTC)
        current_value = current_value.astimezone(UTC)
    return current_value < previous_value


def _assert_replay_matches(
    *,
    observation: CollectionObservation,
    representation: Representation,
    process: Process,
    version: RepresentationVersion,
    hit: _PreparedHit,
    source: SourceKey,
) -> None:
    same_origin = (
        representation.source == source
        and representation.tribunal == hit.tribunal
        and representation.source_id == hit.source_id
    )
    if same_origin and process.numero_cnj != hit.numero_cnj:
        raise SourceIdentityConflict(
            "A mesma identidade de origem já está vinculada a outro número CNJ."
        )
    if not same_origin or process.numero_cnj != hit.numero_cnj:
        raise ObservationPositionConflict(
            "A posição da página já foi observada com outra representação ou processo."
        )
    if version.payload_sha256 == hit.payload_sha256:
        if canonical_json_bytes(cast(JSONValue, version.raw_payload)) != canonical_json_bytes(
            hit.raw_payload
        ):
            raise PayloadHashCollisionError(
                "O mesmo hash SHA-256 foi associado a payloads canônicos diferentes."
            )
    elif version.payload_sha256 != hit.payload_sha256:
        raise ObservationPositionConflict(
            "A posição da página já foi observada com conteúdo diferente."
        )
    if observation.representation_id != representation.id or observation.version_id != version.id:
        raise ObservationPositionConflict("A posição da página aponta para outra versão.")
