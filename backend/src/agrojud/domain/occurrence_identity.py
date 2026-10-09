"""Versioned, representation-scoped technical identity for source movements.

These identities describe source observations only. They do not assert that an
observation is a new or legally distinct act.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from agrojud.domain.canonical_json import JSONValue, canonical_json_bytes

NORMALIZER_VERSION = "movement-normalizer-v1"
_MOVEMENT_FIELDS = frozenset(
    {"codigo", "dataHora", "nome", "orgaoJulgador", "complementosTabelados"}
)
_COMPACT_TIMESTAMP = re.compile(r"^\d{14}$")


class OccurrenceStatus(StrEnum):
    """Technical state of an occurrence relative to one snapshot and its history."""

    FIRST_OBSERVED = "FIRST_OBSERVED"
    KNOWN = "KNOWN"
    NOT_PRESENT_IN_SNAPSHOT = "NOT_PRESENT_IN_SNAPSHOT"
    ALTERATION_OBSERVED = "ALTERATION_OBSERVED"


class IdentityIntegrityError(ValueError):
    """Raised when distinct normalized values claim the same SHA-256 digest."""


class NormalizerVersionMismatch(ValueError):
    """Raised when a snapshot is compared with history from another algorithm."""


@dataclass(frozen=True, slots=True, order=True)
class RepresentationRef:
    """Opaque stable key for one source representation; it is never a CNJ number."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("A representação precisa de uma referência não vazia.")


@dataclass(frozen=True, slots=True, order=True)
class OccurrenceIdentity:
    """Exact technical identity: representation, normalized hash, and multiplicity."""

    representation: RepresentationRef
    content_sha256: str
    ordinal: int

    def __post_init__(self) -> None:
        if len(self.content_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.content_sha256
        ):
            raise ValueError("O hash de conteúdo precisa ser SHA-256 hexadecimal.")
        if self.ordinal < 1:
            raise ValueError("O ordinal de multiplicidade começa em 1.")


@dataclass(frozen=True, slots=True)
class NormalizedMovement:
    """Immutable normalized content plus its comparison-only auxiliary key."""

    algorithm_version: str
    content_sha256: str
    canonical_content: bytes
    auxiliary_key: str | None

    @property
    def content_json(self) -> str:
        """Canonical normalized JSON, including preserved original source values."""

        return self.canonical_content.decode("utf-8")


@dataclass(frozen=True, slots=True)
class ObservedOccurrence:
    identity: OccurrenceIdentity
    movement: NormalizedMovement


@dataclass(frozen=True, slots=True)
class OccurrenceHistory:
    """Append-only history of identities observed for exactly one representation."""

    representation: RepresentationRef
    algorithm_version: str
    occurrences: tuple[ObservedOccurrence, ...] = ()


@dataclass(frozen=True, slots=True)
class OccurrenceObservation:
    identity: OccurrenceIdentity
    status: OccurrenceStatus


@dataclass(frozen=True, slots=True)
class AlterationObserved:
    """Potential changed-content correspondence, with ambiguity left explicit."""

    auxiliary_key: str
    previous: tuple[OccurrenceIdentity, ...]
    current: tuple[OccurrenceIdentity, ...]
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    observations: tuple[OccurrenceObservation, ...]
    alterations: tuple[AlterationObserved, ...]
    history: OccurrenceHistory


@dataclass(frozen=True, slots=True)
class _Timestamp:
    status: str
    normalized: str | None
    comparison_value: JSONValue


def normalize_movement(movement: Mapping[str, JSONValue]) -> NormalizedMovement:
    """Normalize one DataJud-shaped movement without discarding its source values.

    Unknown source fields are retained in ``camposExtras``. Complement records are
    sorted by canonical JSON for comparison, preserving duplicate complements.
    """

    if not isinstance(movement, Mapping):
        raise TypeError("O movimento precisa ser um objeto JSON.")

    normalized: dict[str, JSONValue] = {}
    for field in ("codigo", "nome", "orgaoJulgador"):
        if field in movement:
            normalized[field] = movement[field]

    timestamp: _Timestamp | None = None
    if "dataHora" in movement:
        source_timestamp = movement["dataHora"]
        timestamp = _normalize_timestamp(source_timestamp)
        normalized["dataHora"] = source_timestamp
        normalized["dataHoraStatus"] = timestamp.status
        if timestamp.normalized is not None:
            normalized["dataHoraNormalizada"] = timestamp.normalized

    complements: JSONValue | None = None
    if "complementosTabelados" in movement:
        complements = _sort_complements(movement["complementosTabelados"])
        normalized["complementosTabelados"] = complements

    normalized["camposExtras"] = {
        key: value for key, value in movement.items() if key not in _MOVEMENT_FIELDS
    }
    content = canonical_json_bytes(normalized)
    content_sha256 = hashlib.sha256(content).hexdigest()

    auxiliary_key: str | None = None
    if (
        "codigo" in movement
        and movement["codigo"] is not None
        and timestamp is not None
        and movement["dataHora"] is not None
    ):
        auxiliary = {
            "codigo": movement["codigo"],
            "dataHora": timestamp.comparison_value,
            "orgaoJulgador": _present_value(movement, "orgaoJulgador"),
            "complementosTabelados": _present_value(
                {"complementosTabelados": complements}
                if "complementosTabelados" in movement
                else {},
                "complementosTabelados",
            ),
        }
        auxiliary_key = canonical_json_bytes(auxiliary).decode("utf-8")

    return NormalizedMovement(
        algorithm_version=NORMALIZER_VERSION,
        content_sha256=content_sha256,
        canonical_content=content,
        auxiliary_key=auxiliary_key,
    )


def reconcile_snapshot(
    representation: RepresentationRef,
    movements: Iterable[NormalizedMovement],
    history: OccurrenceHistory | None = None,
) -> ReconciliationResult:
    """Reconcile a complete snapshot as a multiset against retained history.

    The returned history retains absent occurrences. Only an exact identity is
    ``KNOWN``; a changed content with a matching auxiliary key yields an alteration
    reference, never a merge. No array index participates in identity.
    """

    current_movements = tuple(movements)
    algorithm_version = (
        current_movements[0].algorithm_version if current_movements else NORMALIZER_VERSION
    )
    if any(item.algorithm_version != algorithm_version for item in current_movements):
        raise NormalizerVersionMismatch("Um snapshot não pode misturar versões do normalizador.")

    previous = history or OccurrenceHistory(representation, algorithm_version)
    if previous.representation != representation:
        raise ValueError("O histórico pertence a outra representação.")
    if previous.algorithm_version != algorithm_version:
        raise NormalizerVersionMismatch(
            "A comparação exige reprocessamento explícito com uma única versão do normalizador."
        )

    historical_by_identity: dict[OccurrenceIdentity, ObservedOccurrence] = {}
    historical_content_by_hash: dict[str, bytes] = {}
    for occurrence in previous.occurrences:
        _validate_occurrence(occurrence, representation, algorithm_version)
        existing = historical_by_identity.get(occurrence.identity)
        if existing is not None:
            raise ValueError("O histórico contém uma identidade repetida.")
        _check_digest_content(
            historical_content_by_hash,
            occurrence.identity.content_sha256,
            occurrence.movement.canonical_content,
        )
        historical_by_identity[occurrence.identity] = occurrence

    counts: Counter[str] = Counter()
    current_by_hash: dict[str, NormalizedMovement] = {}
    for movement in current_movements:
        _validate_movement(movement, algorithm_version)
        _check_digest_content(
            historical_content_by_hash, movement.content_sha256, movement.canonical_content
        )
        current_existing = current_by_hash.get(movement.content_sha256)
        if (
            current_existing is not None
            and current_existing.canonical_content != movement.canonical_content
        ):
            raise IdentityIntegrityError("Colisão de hash entre conteúdos normalizados distintos.")
        current_by_hash[movement.content_sha256] = movement
        counts[movement.content_sha256] += 1

    current_by_identity: dict[OccurrenceIdentity, ObservedOccurrence] = {}
    for content_hash, count in counts.items():
        movement = current_by_hash[content_hash]
        for ordinal in range(1, count + 1):
            identity = OccurrenceIdentity(representation, content_hash, ordinal)
            current_by_identity[identity] = ObservedOccurrence(identity, movement)

    current_identities = set(current_by_identity)
    altered_current: set[OccurrenceIdentity] = set()
    alteration_groups: dict[str, set[OccurrenceIdentity]] = defaultdict(set)

    historical_contents = {
        (item.movement.auxiliary_key, item.movement.canonical_content)
        for item in historical_by_identity.values()
    }
    for identity, occurrence in current_by_identity.items():
        movement = occurrence.movement
        auxiliary_key = movement.auxiliary_key
        if identity in historical_by_identity or auxiliary_key is None:
            continue
        if (auxiliary_key, movement.canonical_content) in historical_contents:
            continue
        candidates = tuple(
            old
            for old in historical_by_identity.values()
            if old.movement.auxiliary_key == auxiliary_key
            and old.movement.canonical_content != movement.canonical_content
        )
        if candidates:
            altered_current.add(identity)
            alteration_groups[auxiliary_key].update(
                current_identity
                for current_identity, current in current_by_identity.items()
                if current.movement.auxiliary_key == auxiliary_key
            )

    alterations: list[AlterationObserved] = []
    for auxiliary_key, current_group in alteration_groups.items():
        current_movements_in_group = {
            current_by_identity[identity].movement.canonical_content for identity in current_group
        }
        previous_group = tuple(
            sorted(
                old.identity
                for old in historical_by_identity.values()
                if old.movement.auxiliary_key == auxiliary_key
                and any(
                    old.movement.canonical_content != content
                    for content in current_movements_in_group
                )
            )
        )
        current_tuple = tuple(sorted(current_group))
        alterations.append(
            AlterationObserved(
                auxiliary_key=auxiliary_key,
                previous=previous_group,
                current=current_tuple,
                ambiguous=len(previous_group) != 1 or len(current_tuple) != 1,
            )
        )

    observations: list[OccurrenceObservation] = []
    for identity in sorted(current_by_identity):
        if identity in historical_by_identity:
            status = OccurrenceStatus.KNOWN
        elif identity in altered_current:
            status = OccurrenceStatus.ALTERATION_OBSERVED
        else:
            status = OccurrenceStatus.FIRST_OBSERVED
        observations.append(OccurrenceObservation(identity, status))

    for identity in sorted(set(historical_by_identity) - current_identities):
        observations.append(
            OccurrenceObservation(identity, OccurrenceStatus.NOT_PRESENT_IN_SNAPSHOT)
        )
    observations.sort(key=lambda item: item.identity)
    alterations.sort(key=lambda item: item.auxiliary_key)

    updated_records = dict(historical_by_identity)
    updated_records.update(current_by_identity)
    updated_history = OccurrenceHistory(
        representation=representation,
        algorithm_version=algorithm_version,
        occurrences=tuple(updated_records[key] for key in sorted(updated_records)),
    )
    return ReconciliationResult(tuple(observations), tuple(alterations), updated_history)


def _normalize_timestamp(value: JSONValue) -> _Timestamp:
    if value is None:
        return _Timestamp("null", None, None)
    if not isinstance(value, str):
        return _Timestamp("unparseable", None, value)

    parsed: datetime | None = None
    if _COMPACT_TIMESTAMP.fullmatch(value):
        try:
            parsed = datetime.strptime(value, "%Y%m%d%H%M%S")
        except ValueError:
            parsed = None
    elif "T" in value or " " in value:
        iso_value = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(iso_value)
        except ValueError:
            parsed = None

    if parsed is None:
        return _Timestamp("unparseable", None, value)
    if parsed.utcoffset() is None:
        normalized = parsed.isoformat(timespec="microseconds")
        return _Timestamp("timezone_ambiguous", normalized, value)

    utc_value = parsed.astimezone(UTC)
    normalized_utc = utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return _Timestamp("timezone_aware", normalized_utc, normalized_utc)


def _sort_complements(value: JSONValue) -> JSONValue:
    if isinstance(value, list):
        return sorted(value, key=canonical_json_bytes)
    return value


def _present_value(value: Mapping[str, JSONValue], key: str) -> JSONValue:
    if key not in value:
        return {"present": False}
    return {"present": True, "value": value[key]}


def _check_digest_content(
    known: dict[str, bytes], content_hash: str, canonical_content: bytes
) -> None:
    existing = known.get(content_hash)
    if existing is not None and existing != canonical_content:
        raise IdentityIntegrityError("Colisão de hash entre conteúdos normalizados distintos.")
    known[content_hash] = canonical_content


def _validate_occurrence(
    occurrence: ObservedOccurrence,
    representation: RepresentationRef,
    algorithm_version: str,
) -> None:
    if occurrence.identity.representation != representation:
        raise ValueError("O histórico contém ocorrência de outra representação.")
    if occurrence.identity.content_sha256 != occurrence.movement.content_sha256:
        raise ValueError("O hash da identidade diverge do conteúdo normalizado.")
    if occurrence.movement.algorithm_version != algorithm_version:
        raise NormalizerVersionMismatch("O histórico contém outra versão do normalizador.")
    _validate_movement(occurrence.movement, algorithm_version)


def _validate_movement(movement: NormalizedMovement, algorithm_version: str) -> None:
    if movement.algorithm_version != algorithm_version:
        raise NormalizerVersionMismatch("Versão de normalizador inconsistente.")
    actual_hash = hashlib.sha256(movement.canonical_content).hexdigest()
    if actual_hash != movement.content_sha256:
        raise ValueError("O conteúdo normalizado não corresponde ao hash registrado.")
    try:
        json.loads(movement.canonical_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("O conteúdo normalizado não contém JSON válido.") from error
