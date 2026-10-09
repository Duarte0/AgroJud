"""Persist normalized movement snapshots without taking transaction ownership."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy.orm import Session

from agrojud.db.models import MovementSnapshot
from agrojud.db.movement_repositories import MovementRepository
from agrojud.domain.canonical_json import JSONValue, canonical_json_bytes, sha256_json
from agrojud.domain.movement_normalization import (
    MOVEMENT_NORMALIZERS,
    MovementNormalizationIssue,
    MovementValidationError,
    NormalizedMovementSnapshot,
    normalize_movement_snapshot,
)
from agrojud.domain.occurrence_identity import (
    NORMALIZER_VERSION,
    OccurrenceIdentity,
    OccurrenceStatus,
    RepresentationRef,
    reconcile_snapshot,
)


@dataclass(frozen=True, slots=True)
class PersistedMovementSnapshot:
    """Stored movement snapshot plus the diagnostics that require quarantine."""

    snapshot: MovementSnapshot
    issues: tuple[MovementNormalizationIssue, ...]


def persist_movement_snapshot(
    session: Session,
    *,
    representation_id: UUID,
    version_id: UUID,
    raw_source: Mapping[str, JSONValue],
    observed_at: datetime,
    first_observed_at: datetime | None = None,
    normalizer_version: str = NORMALIZER_VERSION,
) -> PersistedMovementSnapshot:
    """Reconcile and persist a movement snapshot inside the caller transaction.

    Each payload version and normalizer pair is immutable. Incomplete snapshots
    retain valid movements and comparison results but never assert that historical
    occurrences absent from the valid subset disappeared.
    """

    occurrence_first_observed_at = first_observed_at or observed_at
    if (
        observed_at.tzinfo is None
        or observed_at.utcoffset() is None
        or occurrence_first_observed_at.tzinfo is None
        or occurrence_first_observed_at.utcoffset() is None
    ):
        raise ValueError("Os horários do snapshot e da primeira observação precisam conter fuso.")

    normalized = normalize_movement_snapshot(raw_source, normalizer_version=normalizer_version)
    repository = MovementRepository(session)
    result_sha256 = _normalization_digest(normalized)
    existing = repository.snapshot(version_id, normalizer_version)
    if existing is not None:
        if existing.result_sha256 != result_sha256:
            raise ValueError(
                "O resultado do normalizador mudou sem alteração de versão; "
                "use uma nova versão explícita."
            )
        return PersistedMovementSnapshot(existing, normalized.issues)

    representation = RepresentationRef(str(representation_id))
    history = repository.history(representation_id, normalizer_version)
    reconciliation = reconcile_snapshot(
        representation,
        normalized.movements,
        history,
        algorithm_version=normalized.normalizer_version,
    )
    raw_by_identity = _raw_by_identity(
        raw_source,
        normalized,
        representation=representation,
    )

    stored_by_identity: dict[OccurrenceIdentity, UUID] = {}
    for occurrence in reconciliation.history.occurrences:
        raw_movement = raw_by_identity.get(occurrence.identity)
        if raw_movement is None:
            existing_occurrence = repository.by_identity(occurrence.identity, normalizer_version)
            if existing_occurrence is None:
                raise RuntimeError("Uma ocorrência histórica não pôde ser localizada.")
            stored_by_identity[occurrence.identity] = existing_occurrence.id
            continue
        stored, _created = repository.get_or_create_occurrence(
            representation_id=representation_id,
            occurrence=occurrence,
            raw_movement=raw_movement,
            first_observed_at=occurrence_first_observed_at,
        )
        stored_by_identity[occurrence.identity] = stored.id

    statuses = {item.identity: item.status for item in reconciliation.observations}
    alterations = {
        identity: alteration
        for alteration in reconciliation.alterations
        for identity in alteration.current
    }
    associations: list[tuple[UUID, bool, str, dict[str, Any] | None]] = []
    for identity, status in statuses.items():
        present = status != OccurrenceStatus.NOT_PRESENT_IN_SNAPSHOT
        if not normalized.complete and not present:
            continue
        detail: dict[str, Any] | None = None
        alteration = alterations.get(identity)
        if alteration is not None:
            detail = {
                "auxiliary_key": alteration.auxiliary_key,
                "previous": [_identity_json(item) for item in alteration.previous],
                "current": [_identity_json(item) for item in alteration.current],
                "ambiguous": alteration.ambiguous,
            }
        associations.append(
            (
                stored_by_identity[identity],
                present,
                status.value,
                detail,
            )
        )

    snapshot = repository.create_snapshot(
        representation_id=representation_id,
        version_id=version_id,
        normalizer_version=normalizer_version,
        is_complete=normalized.complete,
        rejection_count=len(normalized.issues),
        result_sha256=result_sha256,
        processed_at=observed_at.astimezone(UTC),
    )
    repository.create_snapshot_associations(
        snapshot_id=snapshot.id,
        representation_id=representation_id,
        associations=associations,
    )
    return PersistedMovementSnapshot(snapshot, normalized.issues)


def _normalization_digest(normalized: NormalizedMovementSnapshot) -> str:
    return sha256_json(
        {
            "normalizer_version": normalized.normalizer_version,
            "complete": normalized.complete,
            "movements": [
                {
                    "content_sha256": movement.content_sha256,
                    "content": cast(JSONValue, json.loads(movement.content_json)),
                }
                for movement in normalized.movements
            ],
            "issues": [
                {"error_path": issue.error_path, "validation_code": issue.validation_code}
                for issue in normalized.issues
            ],
        }
    )


def _raw_by_identity(
    raw_source: Mapping[str, JSONValue],
    normalized: NormalizedMovementSnapshot,
    *,
    representation: RepresentationRef,
) -> dict[OccurrenceIdentity, JSONValue]:
    grouped: dict[str, list[tuple[bytes, JSONValue, Any]]] = defaultdict(list)
    # Sorting raw JSON makes the retained evidence for indistinguishable duplicates
    # deterministic without using their array position as part of their identity.
    normalizer = MOVEMENT_NORMALIZERS[normalized.normalizer_version]
    raw_movements = raw_source.get("movimentos")
    if not isinstance(raw_movements, list):
        return {}
    for raw_movement in raw_movements:
        try:
            movement = normalizer(raw_movement)
        except MovementValidationError:
            continue
        grouped[movement.content_sha256].append(
            (canonical_json_bytes(raw_movement), raw_movement, movement)
        )

    valid_count = sum(len(values) for values in grouped.values())
    if valid_count != len(normalized.movements):
        raise RuntimeError("A contagem normalizada mudou durante a persistência do snapshot.")

    by_identity: dict[OccurrenceIdentity, JSONValue] = {}
    for content_sha256, values in grouped.items():
        for ordinal, (_raw_bytes, raw_movement, _movement) in enumerate(
            sorted(values, key=lambda item: item[0]), start=1
        ):
            by_identity[
                OccurrenceIdentity(
                    representation,
                    content_sha256,
                    ordinal,
                )
            ] = raw_movement
    return by_identity


def _identity_json(identity: OccurrenceIdentity) -> dict[str, JSONValue]:
    return {
        "content_sha256": identity.content_sha256,
        "ordinal": identity.ordinal,
    }
