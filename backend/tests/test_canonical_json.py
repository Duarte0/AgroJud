from __future__ import annotations

import math

import pytest

from agrojud.domain.canonical_json import canonical_json_bytes, sha256_json


def test_canonical_json_is_stable_and_ignores_object_key_order_and_whitespace() -> None:
    left = {"z": [1, {"b": "ação", "a": True}], "a": None}
    right = {"a": None, "z": [1, {"a": True, "b": "ação"}]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_json_bytes(left) == canonical_json_bytes(left)
    assert sha256_json(left) == sha256_json(right)
    assert b" " not in canonical_json_bytes(left)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({}, {"campo": None}),
        ({"campo": 1}, {"campo": "1"}),
        ({"campo": 1}, {"campo": 1.0}),
        ({"campo": True}, {"campo": 1}),
        ([1, 2], [2, 1]),
    ],
)
def test_canonical_json_distinguishes_missing_null_types_and_array_order(
    left: object, right: object
) -> None:
    assert canonical_json_bytes(left) != canonical_json_bytes(right)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError, match="não finitos"):
        canonical_json_bytes({"value": value})


def test_canonical_json_rejects_non_string_object_keys() -> None:
    with pytest.raises(TypeError, match="Chaves de objetos JSON"):
        canonical_json_bytes({1: "value"})  # type: ignore[dict-item]
