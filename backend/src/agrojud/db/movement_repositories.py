"""Persistence helpers for movement history and data quarantine."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from agrojud.db.models import (
    MovementOccurrence,
    MovementSnapshot,
    MovementSnapshotOccurrence,
    QuarantineRejection,
    QuarantineResolution,
)
from agrojud.domain.canonical_json import JSONValue, canonical_json_bytes
from agrojud.domain.occurrence_identity import (
    NormalizedMovement,
    ObservedOccurrence,
    OccurrenceHistory,
    OccurrenceIdentity,
    RepresentationRef,
)
from agrojud.domain.persistence import MovementHashCollisionError, QuarantinePositionConflict


class MovementRepository:
    """Read and append versioned movement identities and snapshot associations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def history(self, representation_id: UUID, normalizer_version: str) -> OccurrenceHistory:
        rows = self.session.scalars(
            select(MovementOccurrence)
            .where(
                MovementOccurrence.representation_id == representation_id,
                MovementOccurrence.normalizer_version == normalizer_version,
            )
            .order_by(
                MovementOccurrence.content_sha256,
                MovementOccurrence.multiplicity_ordinal,
            )
        ).all()
        reference = RepresentationRef(str(representation_id))
        occurrences: list[ObservedOccurrence] = []
        for row in rows:
            normalized_json = cast(JSONValue, row.normalized_content)
            movement = NormalizedMovement(
                algorithm_version=row.normalizer_version,
                content_sha256=row.content_sha256,
                canonical_content=canonical_json_bytes(normalized_json),
                auxiliary_key=row.auxiliary_key,
            )
            identity = OccurrenceIdentity(
                representation=reference,
                content_sha256=row.content_sha256,
                ordinal=row.multiplicity_ordinal,
            )
            occurrences.append(ObservedOccurrence(identity, movement))
        return OccurrenceHistory(reference, normalizer_version, tuple(occurrences))

    def get_or_create_occurrence(
        self,
        *,
        representation_id: UUID,
        occurrence: ObservedOccurrence,
        raw_movement: JSONValue,
        first_observed_at: datetime,
    ) -> tuple[MovementOccurrence, bool]:
        normalized_json = json.loads(occurrence.movement.content_json)
        if not isinstance(normalized_json, dict):
            raise ValueError("O conteúdo normalizado do movimento precisa ser um objeto JSON.")
        movement_mapping = raw_movement if isinstance(raw_movement, Mapping) else {}
        source_date_present = "dataHora" in movement_mapping
        source_date_original = movement_mapping.get("dataHora") if source_date_present else None
        normalized_date = normalized_json.get("dataHoraNormalizada")
        source_date_status = cast(str, normalized_json.get("dataHoraStatus", "missing"))
        source_date = _parse_normalized_source_date(normalized_date, source_date_status)

        values = {
            "id": uuid4(),
            "representation_id": representation_id,
            "normalizer_version": occurrence.movement.algorithm_version,
            "content_sha256": occurrence.identity.content_sha256,
            "multiplicity_ordinal": occurrence.identity.ordinal,
            "raw_movement": raw_movement,
            "normalized_content": cast(dict[str, Any], normalized_json),
            "auxiliary_key": occurrence.movement.auxiliary_key,
            "source_date_present": source_date_present,
            "source_date_original": source_date_original,
            "source_date_status": source_date_status,
            "source_date_normalized": source_date,
            "first_observed_at": first_observed_at.astimezone(UTC),
        }
        occurrence_id = self.session.execute(
            pg_insert(MovementOccurrence)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    MovementOccurrence.representation_id,
                    MovementOccurrence.normalizer_version,
                    MovementOccurrence.content_sha256,
                    MovementOccurrence.multiplicity_ordinal,
                ]
            )
            .returning(MovementOccurrence.id)
        ).scalar_one_or_none()
        if occurrence_id is not None:
            stored = self.session.get(MovementOccurrence, occurrence_id)
            if stored is None:  # pragma: no cover - inserted in this transaction
                raise RuntimeError("A ocorrência recém-criada não pôde ser lida.")
            return stored, True

        stored = self.session.execute(
            select(MovementOccurrence)
            .where(
                MovementOccurrence.representation_id == representation_id,
                MovementOccurrence.normalizer_version == occurrence.movement.algorithm_version,
                MovementOccurrence.content_sha256 == occurrence.identity.content_sha256,
                MovementOccurrence.multiplicity_ordinal == occurrence.identity.ordinal,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if stored is None:  # pragma: no cover - ON CONFLICT waits for concurrent insert
            raise RuntimeError("A ocorrência existente não pôde ser localizada.")
        stored_json = cast(JSONValue, stored.normalized_content)
        if canonical_json_bytes(stored_json) != occurrence.movement.canonical_content:
            raise MovementHashCollisionError(
                "O mesmo fingerprint de movimento foi associado a conteúdos diferentes."
            )
        return stored, False

    def by_identity(
        self, identity: OccurrenceIdentity, normalizer_version: str
    ) -> MovementOccurrence | None:
        return self.session.execute(
            select(MovementOccurrence).where(
                MovementOccurrence.representation_id == UUID(identity.representation.value),
                MovementOccurrence.normalizer_version == normalizer_version,
                MovementOccurrence.content_sha256 == identity.content_sha256,
                MovementOccurrence.multiplicity_ordinal == identity.ordinal,
            )
        ).scalar_one_or_none()

    def snapshot(self, version_id: UUID, normalizer_version: str) -> MovementSnapshot | None:
        return self.session.execute(
            select(MovementSnapshot)
            .where(
                MovementSnapshot.version_id == version_id,
                MovementSnapshot.normalizer_version == normalizer_version,
            )
            .with_for_update()
        ).scalar_one_or_none()

    def create_snapshot(
        self,
        *,
        representation_id: UUID,
        version_id: UUID,
        normalizer_version: str,
        is_complete: bool,
        rejection_count: int,
        result_sha256: str,
        processed_at: datetime,
    ) -> MovementSnapshot:
        snapshot = MovementSnapshot(
            id=uuid4(),
            representation_id=representation_id,
            version_id=version_id,
            normalizer_version=normalizer_version,
            is_complete=is_complete,
            rejection_count=rejection_count,
            result_sha256=result_sha256,
            processed_at=processed_at.astimezone(UTC),
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def create_snapshot_associations(
        self,
        *,
        snapshot_id: UUID,
        representation_id: UUID,
        associations: list[tuple[UUID, bool, str, dict[str, Any] | None]],
    ) -> None:
        self.session.add_all(
            MovementSnapshotOccurrence(
                id=uuid4(),
                snapshot_id=snapshot_id,
                representation_id=representation_id,
                occurrence_id=occurrence_id,
                present=present,
                comparison_result=result,
                comparison_detail=detail,
            )
            for occurrence_id, present, result, detail in associations
        )
        self.session.flush()


class QuarantineRepository:
    """Create immutable diagnostics and append resolution audit events."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def by_id(self, rejection_id: UUID, *, lock: bool = False) -> QuarantineRejection | None:
        statement = select(QuarantineRejection).where(QuarantineRejection.id == rejection_id)
        if lock:
            statement = statement.with_for_update()
        return self.session.execute(statement).scalar_one_or_none()

    def by_position(
        self, collection_id: UUID, page_key: str, hit_ordinal: int
    ) -> tuple[QuarantineRejection, ...]:
        return tuple(
            self.session.scalars(
                select(QuarantineRejection)
                .where(
                    QuarantineRejection.collection_id == collection_id,
                    QuarantineRejection.page_key == page_key,
                    QuarantineRejection.hit_ordinal == hit_ordinal,
                )
                .order_by(QuarantineRejection.error_path, QuarantineRejection.validation_code)
            ).all()
        )

    def create_or_get(
        self,
        *,
        collection_id: UUID,
        page_key: str,
        hit_ordinal: int,
        error_path: str,
        raw_hit: dict[str, JSONValue],
        validation_code: str,
        normalizer_version: str,
        observed_at: datetime,
    ) -> QuarantineRejection:
        rejection_id = self.session.execute(
            pg_insert(QuarantineRejection)
            .values(
                id=uuid4(),
                collection_id=collection_id,
                page_key=page_key,
                hit_ordinal=hit_ordinal,
                error_path=error_path,
                raw_hit=raw_hit,
                validation_code=validation_code,
                normalizer_version=normalizer_version,
                first_observed_at=observed_at.astimezone(UTC),
                status="pending",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    QuarantineRejection.collection_id,
                    QuarantineRejection.page_key,
                    QuarantineRejection.hit_ordinal,
                    QuarantineRejection.error_path,
                    QuarantineRejection.validation_code,
                    QuarantineRejection.normalizer_version,
                ]
            )
            .returning(QuarantineRejection.id)
        ).scalar_one_or_none()
        if rejection_id is not None:
            rejection = self.by_id(rejection_id)
            if rejection is None:  # pragma: no cover - inserted in this transaction
                raise RuntimeError("A rejeição recém-criada não pôde ser lida.")
            return rejection

        rejection = self.session.execute(
            select(QuarantineRejection)
            .where(
                QuarantineRejection.collection_id == collection_id,
                QuarantineRejection.page_key == page_key,
                QuarantineRejection.hit_ordinal == hit_ordinal,
                QuarantineRejection.error_path == error_path,
                QuarantineRejection.validation_code == validation_code,
                QuarantineRejection.normalizer_version == normalizer_version,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if rejection is None:  # pragma: no cover - ON CONFLICT waits for concurrent insert
            raise RuntimeError("A rejeição existente não pôde ser localizada.")
        if canonical_json_bytes(cast(JSONValue, rejection.raw_hit)) != canonical_json_bytes(
            raw_hit
        ):
            raise QuarantinePositionConflict(
                "A localização da quarentena foi reapresentada com outro conteúdo bruto."
            )
        return rejection

    def resolve(
        self,
        rejection: QuarantineRejection,
        *,
        normalizer_version: str,
        resolved_at: datetime,
        representation_id: UUID,
        version_id: UUID,
        resolution_detail: dict[str, Any],
    ) -> QuarantineResolution:
        resolution = QuarantineResolution(
            id=uuid4(),
            rejection_id=rejection.id,
            normalizer_version=normalizer_version,
            resolved_at=resolved_at.astimezone(UTC),
            representation_id=representation_id,
            version_id=version_id,
            resolution_detail=resolution_detail,
        )
        self.session.add(resolution)
        rejection.status = "resolved"
        rejection.resolved_at = resolved_at.astimezone(UTC)
        self.session.flush()
        return resolution


def _parse_normalized_source_date(value: JSONValue | None, status: str) -> datetime | None:
    if status != "timezone_aware" or not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("A data normalizada com fuso precisa conter offset explícito.")
    return parsed.astimezone(UTC)
