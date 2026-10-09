"""Stable JSON encoding for source payload and normalized-content fingerprints."""

from __future__ import annotations

import hashlib
import json
from math import isfinite

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]


def canonical_json_bytes(value: JSONValue) -> bytes:
    """Encode JSON with sorted object keys and no insignificant whitespace.

    Array order, JSON types, null values, and missing object keys remain meaningful.
    Non-JSON Python values and non-finite floats are rejected instead of being
    stringified or encoded using JavaScript's non-standard NaN/Infinity tokens.
    """

    _validate_json_value(value)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return encoded.encode("utf-8")


def sha256_json(value: JSONValue) -> str:
    """Return the SHA-256 hex digest of :func:`canonical_json_bytes`."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _validate_json_value(value: JSONValue) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("JSON não aceita valores numéricos não finitos.")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Chaves de objetos JSON devem ser texto.")
            _validate_json_value(item)
        return
    raise TypeError("O valor informado não pertence aos tipos JSON suportados.")
