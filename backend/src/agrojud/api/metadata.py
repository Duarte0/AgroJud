"""Environment and versioned preset metadata endpoints."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Request

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.schemas import (
    EnvironmentResponse,
    EvidenceResponse,
    PresetAvailabilityResponse,
    PresetResponse,
    PresetsResponse,
)
from agrojud.config import Settings
from agrojud.sources.catalog import get_item_availability, list_presets

router = APIRouter(prefix="/api/v1", tags=["metadata"])
_REAL_SOURCE_DISABLED_REASON = (
    "A consulta DataJud permanece desabilitada enquanto as validações S1/S2 não forem aprovadas."
)


@router.get(
    "/presets",
    response_model=PresetsResponse,
    responses=ERROR_RESPONSES,
    summary="Lista presets e sua disponibilidade neste ambiente",
)
def get_presets(request: Request) -> PresetsResponse:
    settings: Settings = request.app.state.settings
    mode: str = "real" if settings.environment == "real" else "demo"
    items: list[PresetResponse] = []
    for item in list_presets():
        availability = get_item_availability(item.id, cast(Any, mode))
        items.append(
            PresetResponse(
                id=item.id,
                version=item.version,
                family=item.family,
                name=item.name,
                purpose=item.purpose,
                justification=item.justification,
                filters={dimension: list(codes) for dimension, codes in item.filters.items()},
                include_descendants=item.include_descendants,
                rural_link_status=item.rural_link_status,
                capture_explanation=item.capture_explanation,
                evidence={
                    name: EvidenceResponse(
                        state=evidence.state,
                        reason=evidence.reason,
                        evidence_ids=list(evidence.evidence_ids),
                    )
                    for name, evidence in item.evidence.items()
                },
                required_for_real=list(item.required_for_real),
                availability=PresetAvailabilityResponse(
                    environment=cast(Any, mode),
                    enabled=availability.enabled,
                    reasons=list(availability.reasons),
                    label=availability.label,
                ),
            )
        )
    return PresetsResponse(environment=cast(Any, mode), items=items)


@router.get(
    "/environment",
    response_model=EnvironmentResponse,
    responses=ERROR_RESPONSES,
    summary="Identifica o ambiente e sua fonte selecionada",
)
def get_environment(request: Request) -> EnvironmentResponse:
    settings: Settings = request.app.state.settings
    is_real = settings.environment == "real"
    return EnvironmentResponse(
        environment=settings.environment,
        source="datajud" if is_real else "synthetic",
        source_enabled=not is_real,
        source_disabled_reason=_REAL_SOURCE_DISABLED_REASON if is_real else None,
    )
