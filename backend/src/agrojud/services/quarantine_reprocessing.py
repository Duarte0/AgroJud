"""Explicit-ID local quarantine reprocessing with one transaction per hit."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from agrojud.config import get_settings
from agrojud.db.engine import make_engine
from agrojud.db.models import QuarantineRejection, RepresentationVersion
from agrojud.db.movement_repositories import QuarantineRepository
from agrojud.db.repositories import (
    CollectionRepository,
    ProcessRepository,
    RepresentationRepository,
    VersionRepository,
)
from agrojud.domain.canonical_json import JSONValue
from agrojud.domain.movement_normalization import (
    NORMALIZER_VERSION,
    MovementNormalizationIssue,
    get_movement_normalizer,
    normalize_movement_snapshot,
)
from agrojud.domain.persistence import (
    CollectionNotFoundError,
    QuarantineRejectionNotFoundError,
    SourceIdentityConflict,
)
from agrojud.services.ingestion import (
    NORMALIZER_VERSION as COVER_NORMALIZER_VERSION,
)
from agrojud.services.ingestion import (
    _prepare_hit,
    _RejectedHit,
)
from agrojud.services.movement_ingestion import persist_movement_snapshot
from agrojud.services.news import register_watched_snapshot
from agrojud.sources.contracts import Cursor, SourceHit


@dataclass(frozen=True, slots=True)
class QuarantineReprocessResult:
    rejection_id: UUID
    status: str
    representation_id: UUID | None
    version_id: UUID | None
    created_rejection_ids: tuple[UUID, ...]


def reprocess_quarantine_ids(
    session_factory: sessionmaker[Session],
    rejection_ids: Sequence[UUID],
    *,
    normalizer_version: str = NORMALIZER_VERSION,
    resolved_at: datetime | None = None,
) -> tuple[QuarantineReprocessResult, ...]:
    """Process explicit rejection IDs in separate commit/rollback transactions."""

    if not rejection_ids:
        raise ValueError("Informe ao menos um ID explícito de rejeição.")
    if len(set(rejection_ids)) != len(rejection_ids):
        raise ValueError("Não repita IDs de rejeição no mesmo comando.")
    get_movement_normalizer(normalizer_version)
    timestamp = resolved_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("O horário do reprocessamento precisa conter fuso.")

    results: list[QuarantineReprocessResult] = []
    for rejection_id in rejection_ids:
        with session_factory.begin() as session:
            results.append(
                reprocess_quarantine_hit(
                    session,
                    rejection_id,
                    normalizer_version=normalizer_version,
                    reprocessed_at=timestamp,
                )
            )
    return tuple(results)


def reprocess_quarantine_hit(
    session: Session,
    rejection_id: UUID,
    *,
    normalizer_version: str = NORMALIZER_VERSION,
    reprocessed_at: datetime,
) -> QuarantineReprocessResult:
    """Reprocess one quarantined hit without rewriting collection observations."""

    if not session.in_transaction():
        raise RuntimeError("O reprocessamento exige uma transação por hit.")
    if reprocessed_at.tzinfo is None or reprocessed_at.utcoffset() is None:
        raise ValueError("O horário do reprocessamento precisa conter fuso.")

    quarantine_repository = QuarantineRepository(session)
    rejection = quarantine_repository.by_id(rejection_id)
    if rejection is None:
        raise QuarantineRejectionNotFoundError(
            f"A rejeição {rejection_id} não existe; nenhum item foi varrido."
        )
    collection = CollectionRepository(session).lock(rejection.collection_id)
    if collection is None:
        raise CollectionNotFoundError("A coleta da rejeição informada não existe.")
    rejection = quarantine_repository.by_id(rejection_id, lock=True)
    if rejection is None:  # pragma: no cover - the collection foreign key retains it
        raise QuarantineRejectionNotFoundError(
            f"A rejeição {rejection_id} não existe; nenhum item foi varrido."
        )
    if rejection.status == "resolved":
        return QuarantineReprocessResult(rejection_id, "already_resolved", None, None, ())

    source = "synthetic" if collection.mode == "demo" else "datajud"
    hit = _deserialize_source_hit(rejection.raw_hit)
    prepared = _prepare_hit(hit)
    if isinstance(prepared, _RejectedHit):
        repeated = quarantine_repository.create_or_get(
            collection_id=rejection.collection_id,
            page_key=rejection.page_key,
            hit_ordinal=rejection.hit_ordinal,
            error_path=prepared.issue.error_path,
            raw_hit=prepared.raw_hit,
            validation_code=prepared.issue.validation_code,
            normalizer_version=prepared.issue.normalizer_version,
            observed_at=reprocessed_at,
        )
        return QuarantineReprocessResult(rejection_id, "pending", None, None, (repeated.id,))

    # Fail before any writes if this algorithm is not explicitly implemented.
    normalize_movement_snapshot(
        prepared.raw_payload,
        normalizer_version=normalizer_version,
    )

    process_repository = ProcessRepository(session)
    representation_repository = RepresentationRepository(session)
    version_repository = VersionRepository(session)
    process, _process_created = process_repository.get_or_create(prepared.numero_cnj)
    representation, _representation_created = representation_repository.get_or_create(
        process_id=process.id,
        source=source,
        tribunal=prepared.tribunal,
        source_id=prepared.source_id,
    )
    if representation.process_id != process.id:
        raise SourceIdentityConflict(
            "A mesma identidade de origem já está vinculada a outro número CNJ."
        )

    previous_version = (
        session.get(RepresentationVersion, representation.latest_version_id)
        if representation.latest_version_id is not None
        else None
    )
    version, _version_created = version_repository.get_or_create(
        representation_id=representation.id,
        payload_sha256=prepared.payload_sha256,
        raw_payload=prepared.raw_payload,
        normalizer_version=COVER_NORMALIZER_VERSION,
        source_filed_at_original=prepared.source_filed_at_original,
        source_filed_at=prepared.source_filed_at,
        source_filed_at_timezone_ambiguous=prepared.source_filed_at_timezone_ambiguous,
        source_updated_at_original=prepared.source_updated_at_original,
        source_updated_at=prepared.source_updated_at,
        source_updated_at_timezone_ambiguous=prepared.source_updated_at_timezone_ambiguous,
        observed_at=rejection.first_observed_at,
    )
    has_no_projection = representation.latest_version_id is None
    version_is_newer = (
        previous_version is not None
        and version.id != previous_version.id
        and version.first_observed_at > previous_version.first_observed_at
    )
    if has_no_projection or version_is_newer:
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
            source_filed_at_timezone_ambiguous=prepared.source_filed_at_timezone_ambiguous,
            source_updated_at_original=prepared.source_updated_at_original,
            source_updated_at=prepared.source_updated_at,
            source_updated_at_timezone_ambiguous=prepared.source_updated_at_timezone_ambiguous,
            observed_at=version.first_observed_at,
            subjects=prepared.subjects,
        )

    movement_snapshot = persist_movement_snapshot(
        session,
        representation_id=representation.id,
        version_id=version.id,
        raw_source=prepared.raw_payload,
        observed_at=reprocessed_at,
        first_observed_at=rejection.first_observed_at,
        normalizer_version=normalizer_version,
    )
    register_watched_snapshot(
        session,
        process_id=process.id,
        representation=representation,
        version=version,
        snapshot=movement_snapshot.snapshot,
        provenance="quarantine_reprocess",
        first_observed_at=rejection.first_observed_at,
    )
    new_rejections = tuple(
        quarantine_repository.create_or_get(
            collection_id=rejection.collection_id,
            page_key=rejection.page_key,
            hit_ordinal=rejection.hit_ordinal,
            error_path=issue.error_path,
            raw_hit=prepared.raw_hit,
            validation_code=issue.validation_code,
            normalizer_version=normalizer_version,
            observed_at=reprocessed_at,
        )
        for issue in movement_snapshot.issues
    )
    target_still_rejected = _target_diagnostic_present(rejection, movement_snapshot.issues)
    if target_still_rejected:
        status = "pending"
    else:
        resolution_normalizer_version = (
            COVER_NORMALIZER_VERSION
            if rejection.normalizer_version.startswith("cover-normalizer-")
            else normalizer_version
        )
        quarantine_repository.resolve(
            rejection,
            normalizer_version=resolution_normalizer_version,
            resolved_at=reprocessed_at,
            representation_id=representation.id,
            version_id=version.id,
            resolution_detail={
                "previous_normalizer_version": rejection.normalizer_version,
                "cover_normalizer_version": COVER_NORMALIZER_VERSION,
                "movement_normalizer_version": normalizer_version,
                "movement_snapshot_complete": movement_snapshot.snapshot.is_complete,
                "movement_rejection_count": len(movement_snapshot.issues),
            },
        )
        status = "resolved"

    return QuarantineReprocessResult(
        rejection_id,
        status,
        representation.id,
        version.id,
        tuple(item.id for item in new_rejections),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reprocess explicit local quarantine IDs without external HTTP."
    )
    parser.add_argument(
        "--id",
        dest="rejection_ids",
        required=True,
        action="append",
        type=UUID,
        help="UUID de quarentena; repita para informar vários IDs explícitos.",
    )
    parser.add_argument(
        "--normalizer-version",
        default=NORMALIZER_VERSION,
        help="Versão do normalizador local registrado.",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.effective_database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        results = reprocess_quarantine_ids(
            factory,
            args.rejection_ids,
            normalizer_version=args.normalizer_version,
        )
    finally:
        engine.dispose()
    for result in results:
        print(
            f"rejection_id={result.rejection_id} status={result.status} "
            f"representation_id={result.representation_id} version_id={result.version_id} "
            f"new_rejections={len(result.created_rejection_ids)}"
        )


def _deserialize_source_hit(raw_hit: object) -> SourceHit:
    if not isinstance(raw_hit, dict):
        raise ValueError("O bruto de quarentena não contém um objeto de hit.")
    source_id = raw_hit.get("source_id")
    source = raw_hit.get("source")
    sort_values = raw_hit.get("sort_values")
    raw = raw_hit.get("raw")
    if not isinstance(sort_values, list) or not isinstance(raw, dict):
        raise ValueError("O bruto de quarentena não contém metadados de hit válidos.")
    return SourceHit(
        source_id=cast(JSONValue | None, source_id),
        source=cast(JSONValue | None, source),
        sort_values=cast(Cursor, tuple(sort_values)),
        raw=cast(dict[str, JSONValue], raw),
    )


def _target_diagnostic_present(
    rejection: QuarantineRejection, issues: tuple[MovementNormalizationIssue, ...]
) -> bool:
    return any(
        issue.error_path == rejection.error_path
        and issue.validation_code == rejection.validation_code
        for issue in issues
    )
