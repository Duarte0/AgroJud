"""Versioned validation of movement arrays before persistence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from agrojud.domain.canonical_json import JSONValue
from agrojud.domain.occurrence_identity import (
    NORMALIZER_VERSION as _NORMALIZER_VERSION,
)
from agrojud.domain.occurrence_identity import (
    NormalizedMovement,
    normalize_movement,
)

NORMALIZER_VERSION: str = _NORMALIZER_VERSION


class MovementValidationError(ValueError):
    """A single movement cannot be represented by the selected normalizer."""

    def __init__(self, validation_code: str, message: str) -> None:
        super().__init__(message)
        self.validation_code = validation_code


class UnsupportedMovementNormalizerError(ValueError):
    """The requested normalizer version is not implemented in this process."""


@dataclass(frozen=True, slots=True)
class MovementNormalizationIssue:
    """One localized validation failure within a source movement array."""

    error_path: str
    validation_code: str


@dataclass(frozen=True, slots=True)
class NormalizedMovementSnapshot:
    """Successfully normalized subset and completeness of one source array."""

    normalizer_version: str
    movements: tuple[NormalizedMovement, ...]
    issues: tuple[MovementNormalizationIssue, ...]
    complete: bool


type MovementNormalizer = Callable[[JSONValue], NormalizedMovement]


def _normalize_v1(value: JSONValue) -> NormalizedMovement:
    if not isinstance(value, Mapping):
        raise MovementValidationError(
            "MOVEMENT_NOT_OBJECT", "O item de movimentos precisa ser um objeto JSON."
        )
    return normalize_movement(value)


# A version is accepted only when its implementation is explicitly registered.
# A later correction must add a new version instead of changing v1 in place.
MOVEMENT_NORMALIZERS: dict[str, MovementNormalizer] = {NORMALIZER_VERSION: _normalize_v1}


def get_movement_normalizer(normalizer_version: str) -> MovementNormalizer:
    """Return an explicitly registered implementation or fail before persistence."""

    normalizer = MOVEMENT_NORMALIZERS.get(normalizer_version)
    if normalizer is None:
        raise UnsupportedMovementNormalizerError(
            f"O normalizador de movimentos {normalizer_version!r} não está disponível."
        )
    return normalizer


def normalize_movement_snapshot(
    source: Mapping[str, JSONValue], *, normalizer_version: str = NORMALIZER_VERSION
) -> NormalizedMovementSnapshot:
    """Normalize the valid members of ``movimentos`` and report every rejection.

    An absent, null, or non-array field is incomplete. An explicit empty array is
    complete. Invalid members are localized so other valid movements can still be
    retained without claiming that the snapshot is complete.
    """

    normalizer = get_movement_normalizer(normalizer_version)

    if "movimentos" not in source:
        issue = MovementNormalizationIssue("movimentos", "MOVEMENTS_FIELD_MISSING")
        return NormalizedMovementSnapshot(normalizer_version, (), (issue,), False)

    raw_movements = source["movimentos"]
    if raw_movements is None:
        issue = MovementNormalizationIssue("movimentos", "MOVEMENTS_NULL")
        return NormalizedMovementSnapshot(normalizer_version, (), (issue,), False)
    if not isinstance(raw_movements, list):
        issue = MovementNormalizationIssue("movimentos", "MOVEMENTS_NOT_ARRAY")
        return NormalizedMovementSnapshot(normalizer_version, (), (issue,), False)

    movements: list[NormalizedMovement] = []
    issues: list[MovementNormalizationIssue] = []
    for ordinal, raw_movement in enumerate(raw_movements, start=1):
        try:
            movements.append(normalizer(raw_movement))
        except MovementValidationError as error:
            issues.append(
                MovementNormalizationIssue(f"movimentos[{ordinal - 1}]", error.validation_code)
            )

    return NormalizedMovementSnapshot(
        normalizer_version=normalizer_version,
        movements=tuple(movements),
        issues=tuple(issues),
        complete=not issues,
    )
