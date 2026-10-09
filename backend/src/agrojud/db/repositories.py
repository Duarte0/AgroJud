"""Repositories for page-level, idempotent persistence operations."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from agrojud.db.models import (
    Collection,
    CollectionObservation,
    CollectionResult,
    Process,
    Representation,
    RepresentationSubject,
    RepresentationVersion,
)
from agrojud.domain.canonical_json import JSONValue, canonical_json_bytes, sha256_json
from agrojud.domain.persistence import PayloadHashCollisionError


class ProcessRepository:
    """Create or retrieve a process by its exact CNJ number."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create(self, numero_cnj: str) -> tuple[Process, bool]:
        process_id = self.session.execute(
            pg_insert(Process)
            .values(id=uuid4(), numero_cnj=numero_cnj)
            .on_conflict_do_nothing(index_elements=[Process.numero_cnj])
            .returning(Process.id)
        ).scalar_one_or_none()
        if process_id is not None:
            process = self.session.get(Process, process_id)
            if process is None:  # pragma: no cover - inserted in this transaction
                raise RuntimeError("O processo recém-criado não pôde ser lido.")
            return process, True

        process = self.session.execute(
            select(Process).where(Process.numero_cnj == numero_cnj).with_for_update()
        ).scalar_one_or_none()
        if process is None:  # pragma: no cover - ON CONFLICT waits for a concurrent insert
            raise RuntimeError("O processo existente não pôde ser localizado.")
        return process, False


class RepresentationRepository:
    """Manage stable source identities and their latest local projection."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create(
        self,
        *,
        process_id: UUID,
        source: str,
        tribunal: str,
        source_id: str,
    ) -> tuple[Representation, bool]:
        representation_id = self.session.execute(
            pg_insert(Representation)
            .values(
                id=uuid4(),
                process_id=process_id,
                source=source,
                tribunal=tribunal,
                source_id=source_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    Representation.source,
                    Representation.tribunal,
                    Representation.source_id,
                ]
            )
            .returning(Representation.id)
        ).scalar_one_or_none()
        if representation_id is not None:
            representation = self.session.get(Representation, representation_id)
            if representation is None:  # pragma: no cover - inserted in this transaction
                raise RuntimeError("A representação recém-criada não pôde ser lida.")
            return representation, True

        representation = self.session.execute(
            select(Representation)
            .where(
                Representation.source == source,
                Representation.tribunal == tribunal,
                Representation.source_id == source_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if representation is None:  # pragma: no cover - ON CONFLICT waits for concurrent insert
            raise RuntimeError("A representação existente não pôde ser localizada.")
        return representation, False

    def update_latest_projection(
        self,
        representation: Representation,
        *,
        version: RepresentationVersion,
        class_code: str | None,
        class_name: str | None,
        grau: str | None,
        court_unit_code: str | None,
        court_unit_name: str | None,
        source_filed_at_original: str | None,
        source_filed_at: datetime | None,
        source_filed_at_timezone_ambiguous: bool,
        source_updated_at_original: str | None,
        source_updated_at: datetime | None,
        source_updated_at_timezone_ambiguous: bool,
        observed_at: datetime,
        subjects: Sequence[tuple[str | None, str | None]],
    ) -> None:
        """Replace filterable fields with the payload most recently observed locally."""

        representation.latest_version_id = version.id
        representation.class_code = class_code
        representation.class_name = class_name
        representation.grau = grau
        representation.court_unit_code = court_unit_code
        representation.court_unit_name = court_unit_name
        representation.source_filed_at_original = source_filed_at_original
        representation.source_filed_at = source_filed_at
        representation.source_filed_at_timezone_ambiguous = source_filed_at_timezone_ambiguous
        representation.source_updated_at_original = source_updated_at_original
        representation.source_updated_at = source_updated_at
        representation.source_updated_at_timezone_ambiguous = source_updated_at_timezone_ambiguous
        representation.last_observed_at = observed_at

        self.session.execute(
            delete(RepresentationSubject).where(
                RepresentationSubject.representation_id == representation.id
            )
        )
        self.session.add_all(
            RepresentationSubject(
                id=uuid4(),
                representation_id=representation.id,
                ordinal=ordinal,
                subject_code=subject_code,
                subject_name=subject_name,
            )
            for ordinal, (subject_code, subject_name) in enumerate(subjects, start=1)
        )
        self.session.flush()


class VersionRepository:
    """Reuse immutable payload versions while detecting incompatible hash claims."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_or_create(
        self,
        *,
        representation_id: UUID,
        payload_sha256: str,
        raw_payload: dict[str, JSONValue],
        normalizer_version: str,
        source_filed_at_original: str | None,
        source_filed_at: datetime | None,
        source_filed_at_timezone_ambiguous: bool,
        source_updated_at_original: str | None,
        source_updated_at: datetime | None,
        source_updated_at_timezone_ambiguous: bool,
        observed_at: datetime,
    ) -> tuple[RepresentationVersion, bool]:
        version_id = self.session.execute(
            pg_insert(RepresentationVersion)
            .values(
                id=uuid4(),
                representation_id=representation_id,
                payload_sha256=payload_sha256,
                raw_payload=raw_payload,
                normalizer_version=normalizer_version,
                source_filed_at_original=source_filed_at_original,
                source_filed_at=source_filed_at,
                source_filed_at_timezone_ambiguous=source_filed_at_timezone_ambiguous,
                source_updated_at_original=source_updated_at_original,
                source_updated_at=source_updated_at,
                source_updated_at_timezone_ambiguous=source_updated_at_timezone_ambiguous,
                first_observed_at=observed_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    RepresentationVersion.representation_id,
                    RepresentationVersion.payload_sha256,
                ]
            )
            .returning(RepresentationVersion.id)
        ).scalar_one_or_none()
        if version_id is not None:
            version = self.session.get(RepresentationVersion, version_id)
            if version is None:  # pragma: no cover - inserted in this transaction
                raise RuntimeError("A versão recém-criada não pôde ser lida.")
            return version, True

        version = self.session.execute(
            select(RepresentationVersion)
            .where(
                RepresentationVersion.representation_id == representation_id,
                RepresentationVersion.payload_sha256 == payload_sha256,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if version is None:  # pragma: no cover - ON CONFLICT waits for concurrent insert
            raise RuntimeError("A versão existente não pôde ser localizada.")
        stored_json = cast(JSONValue, version.raw_payload)
        if canonical_json_bytes(stored_json) != canonical_json_bytes(raw_payload):
            raise PayloadHashCollisionError(
                "O mesmo hash SHA-256 foi associado a payloads canônicos diferentes."
            )
        return version, False


class CollectionRepository:
    """Create collection identities and serialize page writes per collection."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        mode: Literal["demo", "real"],
        resolved_criteria: dict[str, JSONValue],
        previous_collection_id: UUID | None = None,
        created_at: datetime | None = None,
    ) -> Collection:
        if mode not in ("demo", "real"):
            raise ValueError("O modo da coleta deve ser demo ou real.")
        if not isinstance(resolved_criteria, dict):
            raise ValueError("Os critérios resolvidos devem ser um objeto JSON.")
        if created_at is not None:
            if created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ValueError("A data de criação da coleta precisa conter fuso.")
            created_at = created_at.astimezone(UTC)
        criteria_hash = sha256_json(cast(JSONValue, resolved_criteria))
        collection = Collection(
            id=uuid4(),
            mode=mode,
            resolved_criteria=cast(dict[str, Any], resolved_criteria),
            criteria_sha256=criteria_hash,
            previous_collection_id=previous_collection_id,
            **({"created_at": created_at} if created_at is not None else {}),
        )
        self.session.add(collection)
        self.session.flush()
        return collection

    def lock(self, collection_id: UUID) -> Collection | None:
        return self.session.execute(
            select(Collection).where(Collection.id == collection_id).with_for_update()
        ).scalar_one_or_none()

    def page_ordinals(self, collection_id: UUID, page_key: str) -> tuple[int, ...]:
        return tuple(
            self.session.scalars(
                select(CollectionObservation.hit_ordinal)
                .where(
                    CollectionObservation.collection_id == collection_id,
                    CollectionObservation.page_key == page_key,
                )
                .order_by(CollectionObservation.hit_ordinal)
            ).all()
        )


class ObservationRepository:
    """Persist hit positions and one result per collection/representation."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def by_position(
        self, collection_id: UUID, page_key: str, hit_ordinal: int
    ) -> tuple[CollectionObservation, Representation, Process, RepresentationVersion] | None:
        row = self.session.execute(
            select(CollectionObservation, Representation, Process, RepresentationVersion)
            .join(Representation, Representation.id == CollectionObservation.representation_id)
            .join(Process, Process.id == Representation.process_id)
            .join(
                RepresentationVersion, RepresentationVersion.id == CollectionObservation.version_id
            )
            .where(
                CollectionObservation.collection_id == collection_id,
                CollectionObservation.page_key == page_key,
                CollectionObservation.hit_ordinal == hit_ordinal,
            )
            .with_for_update(of=CollectionObservation)
        ).one_or_none()
        if row is None:
            return None
        return cast(
            tuple[CollectionObservation, Representation, Process, RepresentationVersion], row
        )

    def by_id(self, observation_id: UUID) -> CollectionObservation:
        observation = self.session.get(CollectionObservation, observation_id)
        if observation is None:  # pragma: no cover - inserted or selected earlier
            raise RuntimeError("A observação esperada não pôde ser lida.")
        return observation

    def create(
        self,
        *,
        collection_id: UUID,
        representation_id: UUID,
        page_key: str,
        hit_ordinal: int,
        version_id: UUID,
        observed_at: datetime,
        capture_outcome: Literal["new", "updated", "unchanged"],
        source_filed_at_regressed: bool,
        regressed_from_version_id: UUID | None,
    ) -> CollectionObservation:
        observation = CollectionObservation(
            id=uuid4(),
            collection_id=collection_id,
            representation_id=representation_id,
            page_key=page_key,
            hit_ordinal=hit_ordinal,
            version_id=version_id,
            observed_at=observed_at,
            capture_outcome=capture_outcome,
            source_filed_at_regressed=source_filed_at_regressed,
            regressed_from_version_id=regressed_from_version_id,
        )
        self.session.add(observation)
        self.session.flush()
        return observation

    def ensure_collection_result(
        self,
        *,
        collection_id: UUID,
        representation_id: UUID,
        first_observation_id: UUID,
        included_at: datetime,
        capture_outcome: Literal["new", "updated", "unchanged"],
    ) -> CollectionResult:
        result_id = self.session.execute(
            pg_insert(CollectionResult)
            .values(
                id=uuid4(),
                collection_id=collection_id,
                representation_id=representation_id,
                first_observation_id=first_observation_id,
                included_at=included_at,
                capture_outcome=capture_outcome,
            )
            .on_conflict_do_nothing(
                index_elements=[CollectionResult.collection_id, CollectionResult.representation_id]
            )
            .returning(CollectionResult.id)
        ).scalar_one_or_none()
        result_id = (
            result_id
            or self.session.execute(
                select(CollectionResult.id).where(
                    CollectionResult.collection_id == collection_id,
                    CollectionResult.representation_id == representation_id,
                )
            ).scalar_one()
        )
        result = self.session.get(CollectionResult, result_id)
        if result is None:  # pragma: no cover - inserted or selected in this transaction
            raise RuntimeError("O resultado da coleta não pôde ser lido.")
        return result
