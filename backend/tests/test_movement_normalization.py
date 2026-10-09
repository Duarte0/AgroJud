"""Unit tests for movement list completeness and localized diagnostics."""

import pytest

from agrojud.domain.movement_normalization import (
    UnsupportedMovementNormalizerError,
    normalize_movement_snapshot,
)


@pytest.mark.parametrize(
    ("source", "complete", "expected"),
    [
        ({}, False, ("movimentos", "MOVEMENTS_FIELD_MISSING")),
        ({"movimentos": None}, False, ("movimentos", "MOVEMENTS_NULL")),
        ({"movimentos": "invalid"}, False, ("movimentos", "MOVEMENTS_NOT_ARRAY")),
        ({"movimentos": []}, True, None),
        (
            {"movimentos": [{"codigo": 1, "nome": "válido"}, 7]},
            False,
            ("movimentos[1]", "MOVEMENT_NOT_OBJECT"),
        ),
    ],
)
def test_movement_array_completeness_is_explicit(
    source: dict[str, object], complete: bool, expected: tuple[str, str] | None
) -> None:
    result = normalize_movement_snapshot(source)  # type: ignore[arg-type]

    assert result.complete is complete
    if expected is None:
        assert result.issues == ()
    else:
        assert tuple((item.error_path, item.validation_code) for item in result.issues) == (
            expected,
        )


def test_explicit_empty_movement_array_is_complete() -> None:
    result = normalize_movement_snapshot({"movimentos": []})

    assert result.complete
    assert result.movements == ()
    assert result.issues == ()


def test_unknown_normalizer_version_fails_explicitly() -> None:
    with pytest.raises(UnsupportedMovementNormalizerError, match="não está disponível"):
        normalize_movement_snapshot({}, normalizer_version="movement-normalizer-unreleased")
