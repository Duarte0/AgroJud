"""Allowlist-backed tribunal selection for DataJud product flows."""

from __future__ import annotations

from dataclasses import dataclass

from agrojud.config import Settings
from agrojud.sources.contracts import SourceError, SourceErrorCode
from agrojud.sources.tribunals import ALLOWLIST_VERSION, TRIBUNAL_ALLOWLIST, canonicalize_tribunal


@dataclass(frozen=True, slots=True)
class TribunalAvailability:
    tribunal: str
    s1_s2_state: str
    preset_state: str | None
    enabled: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class TribunalAvailabilitySnapshot:
    matrix_sha256: str | None
    allowlist_version: str | None
    catalog_version: str | None
    tpu_version: str | None
    selection_enabled: bool
    global_reasons: tuple[str, ...]
    tribunals: tuple[TribunalAvailability, ...]


def tribunal_availability(
    settings: Settings,
    *,
    preset_id: str | None = None,
) -> TribunalAvailabilitySnapshot:
    """List all allowlisted endpoints without consulting SPEC-024 evidence.

    ``settings`` and ``preset_id`` remain in the signature for callers and clients
    that used the previous capability-gated contract. They do not change which
    tribunals can be selected.
    """

    del settings
    has_preset = preset_id is not None
    return TribunalAvailabilitySnapshot(
        matrix_sha256=None,
        allowlist_version=ALLOWLIST_VERSION,
        catalog_version=None,
        tpu_version=None,
        selection_enabled=True,
        global_reasons=(),
        tribunals=tuple(
            TribunalAvailability(
                tribunal=endpoint.canonical_tribunal,
                s1_s2_state="NOT_REQUIRED",
                preset_state="NOT_REQUIRED" if has_preset else None,
                enabled=True,
                reason=None,
            )
            for endpoint in TRIBUNAL_ALLOWLIST.values()
        ),
    )


def require_tribunal_available(
    settings: Settings,
    tribunal: str,
    *,
    preset_id: str | None = None,
) -> TribunalAvailability:
    """Validate the allowlist boundary; do not gate on external evidence."""

    del settings, preset_id
    try:
        canonical = canonicalize_tribunal(tribunal)
    except TypeError, ValueError:
        raise SourceError(
            SourceErrorCode.VALIDATION,
            "O tribunal informado está fora da allowlist do DataJud.",
            field_path="tribunal",
            validation_code="TRIBUNAL_NOT_ALLOWLISTED",
        ) from None
    return TribunalAvailability(
        tribunal=canonical,
        s1_s2_state="NOT_REQUIRED",
        preset_state=None,
        enabled=True,
        reason=None,
    )
