"""Typed HTTP request and response contracts for SPEC-011."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

JobStatus = Literal[
    "queued", "running", "retry_wait", "completed", "partial", "failed", "cancelled"
]
EnvironmentName = Literal["demo", "real", "test"]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaginationResponse[ItemT](APIModel):
    items: list[ItemT]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class DiscoveryCriteria(APIModel):
    preset_id: str = Field(min_length=1, max_length=120)
    filed_from: date | None = None
    filed_through: date | None = None
    page_size: int = Field(default=100, ge=1, le=100)
    hit_budget: int = Field(default=2_000, ge=1, le=2_000)

    @model_validator(mode="after")
    def validate_date_interval(self) -> DiscoveryCriteria:
        if (self.filed_from is None) != (self.filed_through is None):
            raise ValueError("Informe filed_from e filed_through juntos.")
        if (
            self.filed_from is not None
            and self.filed_through is not None
            and self.filed_from > self.filed_through
        ):
            raise ValueError("filed_from deve ser anterior ou igual a filed_through.")
        return self


class RefreshNumberCriteria(APIModel):
    process_number: str = Field(min_length=1, max_length=30)
    page_size: int = Field(default=100, ge=1, le=100)
    hit_budget: int = Field(default=2_000, ge=1, le=2_000)


class DiscoveryJobRequest(APIModel):
    kind: Literal["discovery"]
    criteria: DiscoveryCriteria


class RefreshNumberJobRequest(APIModel):
    kind: Literal["refresh_number"]
    criteria: RefreshNumberCriteria


CreateJobRequest = Annotated[
    DiscoveryJobRequest | RefreshNumberJobRequest,
    Field(discriminator="kind"),
]


class JobCreatedResponse(APIModel):
    job_id: UUID
    collection_id: UUID
    status: JobStatus
    reused: bool


class JobCommandResponse(APIModel):
    job_id: UUID
    collection_id: UUID
    status: JobStatus
    reused: bool = False
    cancel_requested: bool = False
    cursor_invalid: bool = False


class JobCheckpointResponse(APIModel):
    next_page: int = Field(ge=1)
    revision: int = Field(ge=0)
    updated_at: datetime


class JobAttemptResponse(APIModel):
    id: UUID
    attempt_number: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime | None
    outcome: str | None
    error_code: str | None
    error_summary: str | None


class JobEventResponse(APIModel):
    event_number: int = Field(ge=1)
    event_type: str
    details: dict[str, Any]
    created_at: datetime


class JobSummaryResponse(APIModel):
    id: UUID
    collection_id: UUID
    kind: Literal["discovery", "refresh_number"]
    environment: Literal["demo", "real"]
    source: str
    tribunal: str
    status: JobStatus
    coverage: dict[str, Any] | None
    reason: str | None
    cancel_requested: bool
    attempt_count: int = Field(ge=0)
    retry_cycle: int = Field(ge=1)
    next_attempt_at: datetime
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobDetailResponse(JobSummaryResponse):
    criteria: dict[str, Any]
    execution: dict[str, Any]
    checkpoint: JobCheckpointResponse
    cursor_invalid: bool
    predecessor_job_id: UUID | None
    attempts: list[JobAttemptResponse]
    events: list[JobEventResponse]


class ErrorDetail(APIModel):
    location: list[str | int]
    type: str
    message: str


class ErrorBody(APIModel):
    code: str
    message: str
    details: list[ErrorDetail] | dict[str, Any] | None = None
    request_id: str


class ErrorResponse(APIModel):
    error: ErrorBody


class LatestCollectionResponse(APIModel):
    collection_id: UUID
    environment: Literal["demo", "real"]
    included_at: datetime
    capture_outcome: Literal["new", "updated", "unchanged"]
    job_id: UUID | None
    job_status: JobStatus | None


class ProcessSummaryResponse(APIModel):
    id: UUID
    numero_cnj: str = Field(pattern=r"^\d{20}$")
    created_at: datetime
    representation_count: int = Field(ge=0)
    latest_observed_at: datetime | None
    latest_collection: LatestCollectionResponse | None


class MovementDiagnosticResponse(APIModel):
    representation_id: UUID
    available: bool
    is_complete: bool | None
    rejection_count: int | None
    normalizer_version: str | None
    processed_at: datetime | None


class ProcessRepresentationResponse(APIModel):
    id: UUID
    process_id: UUID
    source: str
    tribunal: str
    source_id: str
    latest_version_id: UUID | None
    class_code: str | None
    class_name: str | None
    grau: str | None
    court_unit_code: str | None
    court_unit_name: str | None
    source_filed_at_original: str | None
    source_filed_at: datetime | None
    source_filed_at_timezone_ambiguous: bool
    source_updated_at_original: str | None
    source_updated_at: datetime | None
    source_updated_at_timezone_ambiguous: bool
    last_observed_at: datetime | None
    latest_collection: LatestCollectionResponse | None
    movement_diagnostic: MovementDiagnosticResponse


class ProcessDetailResponse(ProcessSummaryResponse):
    pass


class ProcessMovementResponse(APIModel):
    occurrence_id: UUID
    representation_id: UUID
    content: dict[str, Any]
    source_date_original: Any
    source_date_status: str
    source_date: datetime | None
    multiplicity_ordinal: int = Field(ge=1)
    comparison_result: str
    comparison_detail: dict[str, Any] | None
    first_observed_at: datetime


class ProcessMovementsResponse(PaginationResponse[ProcessMovementResponse]):
    process_id: UUID
    diagnostics: list[MovementDiagnosticResponse]


class EvidenceResponse(APIModel):
    state: Literal["validated", "incompatible", "inconclusive"]
    reason: str
    evidence_ids: list[str]


class PresetAvailabilityResponse(APIModel):
    environment: Literal["demo", "real"]
    enabled: bool
    reasons: list[str]
    label: str


class PresetResponse(APIModel):
    id: str
    version: str
    family: str
    name: str
    purpose: str
    justification: str
    filters: dict[str, list[int]]
    include_descendants: bool
    rural_link_status: str
    capture_explanation: str
    evidence: dict[str, EvidenceResponse]
    required_for_real: list[str]
    availability: PresetAvailabilityResponse


class PresetsResponse(APIModel):
    environment: Literal["demo", "real"]
    items: list[PresetResponse]


class EnvironmentResponse(APIModel):
    environment: EnvironmentName
    source: Literal["synthetic", "datajud"]
    source_enabled: bool
    source_disabled_reason: str | None
