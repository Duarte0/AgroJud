"""Environment and versioned preset metadata endpoints."""

from __future__ import annotations

from typing import Any, Literal, cast

from fastapi import APIRouter, Query, Request

from agrojud.api.errors import ERROR_RESPONSES
from agrojud.api.schemas import (
    EnvironmentResponse,
    EvidenceResponse,
    PresetAvailabilityResponse,
    PresetResponse,
    PresetsResponse,
    SearchTypeCombinationsResponse,
    SearchTypeCriteriaResponse,
    SearchTypeResponse,
    SearchTypesResponse,
    TPUCodeResponse,
)
from agrojud.config import Settings
from agrojud.sources.catalog import (
    get_item_availability,
    list_custom_search_codes,
    list_official_themes,
    list_presets,
)
from agrojud.sources.factory import real_source_unavailable_reason

router = APIRouter(prefix="/api/v1", tags=["metadata"])


@router.get(
    "/search-types",
    response_model=SearchTypesResponse,
    responses=ERROR_RESPONSES,
    summary="Lista temas oficiais disponíveis no Radar",
)
def get_search_types(request: Request) -> SearchTypesResponse:
    settings: Settings = request.app.state.settings
    mode: str = "real" if settings.environment == "real" else "demo"
    items: list[SearchTypeResponse] = []
    for item in list_official_themes():
        availability = get_item_availability(item.id, cast(Any, mode))
        items.append(
            SearchTypeResponse(
                id=item.id,
                version=item.version,
                name=item.name,
                description=item.purpose,
                criteria=SearchTypeCriteriaResponse(
                    classes=list(item.filters["class"]),
                    subjects=list(item.filters["subject"]),
                    movements=list(item.filters["movement"]),
                ),
                combinations=SearchTypeCombinationsResponse(
                    within_dimension="OR",
                    between_dimensions=item.dimension_combination,
                ),
                capture_explanation=item.capture_explanation,
                availability=PresetAvailabilityResponse(
                    environment=cast(Any, mode),
                    enabled=availability.enabled,
                    reasons=list(availability.reasons),
                    label=availability.label,
                ),
            )
        )
    return SearchTypesResponse(environment=cast(Any, mode), items=items)


@router.get(
    "/tpu-codes",
    response_model=list[TPUCodeResponse],
    responses=ERROR_RESPONSES,
    summary="Pesquisa códigos TPU ativos e aprovados para buscas personalizadas",
)
def search_tpu_codes(
    dimension: Literal["class", "subject"],
    q: str = Query(default="", max_length=120),
) -> list[TPUCodeResponse]:
    codes = list_custom_search_codes(cast(Any, dimension), query=q)
    return [
        TPUCodeResponse(
            dimension=cast(Any, code.dimension),
            code=code.code,
            name=code.name,
            tjgo_first_grade=code.tjgo_first_grade,
            tjgo_second_grade=code.tjgo_second_grade,
        )
        for code in codes
    ]


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
        reasons = list(availability.reasons)
        enabled = availability.enabled
        label = availability.label
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
                    enabled=enabled,
                    reasons=reasons,
                    label=label,
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
    disabled_reason = real_source_unavailable_reason(settings)
    return EnvironmentResponse(
        environment=settings.environment,
        source="datajud" if settings.environment == "real" else "synthetic",
        source_enabled=disabled_reason is None,
        source_disabled_reason=disabled_reason,
    )
