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
TriageDecision = Literal["pending", "relevant", "discarded"]
RuralLink = Literal["unconfirmed", "confirmed"]
NewsCategory = Literal["NEW_OBSERVATION", "ALTERATION_OBSERVED", "NEW_REPRESENTATION"]
NewsStatus = Literal["pending", "reviewed"]
NewsProvenance = Literal["ingestion", "quarantine_reprocess"]
ProcessFilterField = Literal[
    "process_number",
    "subject",
    "subject_code",
    "subject_name_exact",
    "class",
    "court_unit",
    "preset_id",
    "collection_id",
    "signal_category",
]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaginationResponse[ItemT](APIModel):
    items: list[ItemT]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class ProcessFilterOptionResponse(APIModel):
    value: str
    label: str
    detail: str | None = None
    process_count: int | None = Field(default=None, ge=0)


class ProcessFilterOptionsResponse(APIModel):
    field: ProcessFilterField
    items: list[ProcessFilterOptionResponse]


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


class SavedSearchFilters(APIModel):
    filed_from: date | None = None
    filed_through: date | None = None
    page_size: int = Field(default=100, ge=1, le=100)
    hit_budget: int = Field(default=2_000, ge=1, le=2_000)

    @model_validator(mode="after")
    def validate_dates(self) -> SavedSearchFilters:
        if (self.filed_from is None) != (self.filed_through is None):
            raise ValueError("Informe filed_from e filed_through juntos.")
        if (
            self.filed_from is not None
            and self.filed_through is not None
            and self.filed_from > self.filed_through
        ):
            raise ValueError("filed_from deve ser anterior ou igual a filed_through.")
        return self


class SavedSearchCreateRequest(APIModel):
    name: str = Field(min_length=1, max_length=160)
    preset_id: str = Field(min_length=1, max_length=120)
    window_mode: Literal["fixed", "rolling_12_months"]
    filters: SavedSearchFilters
    enabled: bool = True

    @model_validator(mode="after")
    def validate_window(self) -> SavedSearchCreateRequest:
        has_dates = self.filters.filed_from is not None
        if (self.window_mode == "fixed") != has_dates:
            raise ValueError("Janela fixa exige datas; rolling_12_months não aceita datas fixas.")
        return self


class SavedSearchPatchRequest(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    preset_id: str | None = Field(default=None, min_length=1, max_length=120)
    window_mode: Literal["fixed", "rolling_12_months"] | None = None
    filters: SavedSearchFilters | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_null_changes(self) -> SavedSearchPatchRequest:
        for field in ("name", "preset_id", "window_mode", "filters", "enabled"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} não pode ser nulo quando informado.")
        return self


class ScheduleDispatchResponse(APIModel):
    id: UUID
    scheduled_for_date: date
    status: Literal["pending", "enqueued", "blocked", "cancelled", "coalesced"]
    job_id: UUID | None
    missed_from: date | None
    missed_through: date | None
    reason: str | None
    updated_at: datetime


class SavedSearchAvailabilityResponse(APIModel):
    enabled: bool
    reasons: list[str]


class SavedSearchResponse(APIModel):
    id: UUID
    name: str
    version: int = Field(ge=1)
    preset_id: str
    preset_version: str
    window_mode: Literal["fixed", "rolling_12_months"]
    filters: SavedSearchFilters
    enabled: bool
    next_run_at: datetime | None
    availability: SavedSearchAvailabilityResponse
    last_dispatch: ScheduleDispatchResponse | None
    created_at: datetime
    updated_at: datetime


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


class ProcessTriageStateResponse(APIModel):
    decision: TriageDecision
    rural_link: RuralLink
    note: str = Field(max_length=5000)
    version: int = Field(ge=0)
    updated_at: datetime | None


class ProcessTriageSnapshotResponse(APIModel):
    decision: TriageDecision
    rural_link: RuralLink
    note: str = Field(max_length=5000)
    version: int = Field(ge=0)


class ProcessTriagePatchRequest(APIModel):
    expected_version: int = Field(ge=0)
    decision: TriageDecision | None = None
    rural_link: RuralLink | None = None
    note: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def reject_null_changes(self) -> ProcessTriagePatchRequest:
        for field in ("decision", "rural_link", "note"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} não pode ser nulo quando informado.")
        return self


class ProcessTriageHistoryEntryResponse(APIModel):
    id: UUID
    version: int = Field(ge=1)
    previous_state: ProcessTriageSnapshotResponse
    new_state: ProcessTriageSnapshotResponse
    origin: Literal["manual"]
    created_at: datetime


class ProcessWatchHistoryResponse(APIModel):
    id: UUID
    action: Literal["included", "removed"]
    created_at: datetime


class RepresentationBaselineResponse(APIModel):
    representation_id: UUID
    source: str
    tribunal: str
    state: Literal["pending", "established"]
    version_id: UUID | None
    established_at: datetime | None


class ProcessRefreshResultResponse(APIModel):
    job_id: UUID
    state: Literal["pending", "found", "absent_in_query", "partial", "failed", "cancelled"]
    job_status: JobStatus
    checked_at: datetime
    hit_count: int | None = Field(default=None, ge=0)


class ProcessWatchResponse(APIModel):
    process_id: UUID
    active: bool
    included_at: datetime | None
    removed_at: datetime | None
    next_run_at: datetime | None
    last_schedule: ScheduleDispatchResponse | None
    history: list[ProcessWatchHistoryResponse]
    baselines: list[RepresentationBaselineResponse]
    last_refresh: ProcessRefreshResultResponse | None


class ProcessNewsResponse(APIModel):
    id: UUID
    process_id: UUID
    numero_cnj: str = Field(pattern=r"^\d{20}$")
    representation_id: UUID
    source: str
    tribunal: str
    source_id: str
    category: NewsCategory
    status: NewsStatus
    event_date: datetime | None
    event_date_original: Any
    event_date_status: str
    first_observed_at: datetime
    evidence: dict[str, Any]
    provenance: NewsProvenance
    created_at: datetime


class ProcessNewsStatusPatchRequest(APIModel):
    status: NewsStatus


class WatchlistItemResponse(APIModel):
    process_id: UUID
    numero_cnj: str = Field(pattern=r"^\d{20}$")
    included_at: datetime
    next_run_at: datetime
    last_refresh: ProcessRefreshResultResponse | None
    last_schedule: ScheduleDispatchResponse | None


class SignalRunFilter(APIModel):
    process_number: str = Field(min_length=1, max_length=30)


class SignalRunRequest(APIModel):
    process_ids: list[UUID] | None = Field(default=None, max_length=2_000)
    filters: SignalRunFilter | None = None
    rule_ids: list[str] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_one_explicit_selection(self) -> SignalRunRequest:
        if (self.process_ids is None) == (self.filters is None):
            raise ValueError("Informe process_ids ou um filtro local.")
        if self.rule_ids is not None and len(set(self.rule_ids)) != len(self.rule_ids):
            raise ValueError("rule_ids não pode conter valores repetidos.")
        return self


class SignalRunAcceptedResponse(APIModel):
    job_id: UUID
    status: JobStatus
    process_count: int = Field(ge=0)
    input_count: int = Field(ge=0)
    reused: bool


class SignalRunStatusResponse(APIModel):
    job_id: UUID
    status: JobStatus
    reason: str | None
    coverage: dict[str, Any] | None
    process_count: int = Field(ge=0)
    input_count: int = Field(ge=0)
    processed_input_count: int = Field(ge=0)
    completed_process_count: int = Field(ge=0)
    stale_process_count: int = Field(ge=0)
    not_evaluated_process_count: int = Field(ge=0)
    resumable: bool
    created_at: datetime
    finished_at: datetime | None


class SignalRuleEnablementResponse(APIModel):
    enabled: bool
    state: str
    reason: str
    evidence_ids: list[str]


class ProcessSignalResponse(APIModel):
    id: UUID
    state: Literal["current", "historical"]
    evidence_stale: bool
    category: str
    rule_id: str
    rule_version: str
    rule_name: str
    explanation: str
    environment: Literal["demo", "real"]
    rule_enablement: SignalRuleEnablementResponse
    representation_id: UUID
    evidence_kind: str
    evidence_id: UUID
    movement_occurrence_id: UUID | None
    evidence_version_id: UUID | None
    evidence_snapshot_id: UUID | None
    run_id: UUID
    evaluated_at: datetime


class ProcessSignalsResponse(APIModel):
    process_id: UUID
    items: list[ProcessSignalResponse]
    latest_run: SignalRunStatusResponse | None = None


class ProcessSummaryResponse(APIModel):
    id: UUID
    numero_cnj: str = Field(pattern=r"^\d{20}$")
    created_at: datetime
    representation_count: int = Field(ge=0)
    latest_observed_at: datetime | None
    latest_collection: LatestCollectionResponse | None
    triage: ProcessTriageStateResponse


class OverviewMetricResponse(APIModel):
    value: int = Field(ge=0)
    unit: Literal["processes", "representations", "occurrences", "jobs"]


class OverviewFiltersResponse(APIModel):
    process_number: str | None = None
    subject: str | None = None
    subject_code: str | None = None
    subject_name_exact: str | None = None
    class_filter: str | None = Field(default=None, alias="class")
    court_unit: str | None = None
    collection_id: UUID | None = None
    preset_id: str | None = None
    decision: TriageDecision | None = None
    rural_link: RuralLink | None = None
    followed: bool | None = None
    pending_news: bool | None = None
    signal_category: str | None = None


class OverviewTriageResponse(APIModel):
    pending: OverviewMetricResponse
    relevant: OverviewMetricResponse
    discarded: OverviewMetricResponse


class OverviewSignalCategoryResponse(APIModel):
    category: str
    processes: OverviewMetricResponse


class OverviewThemeResponse(APIModel):
    subject_code: str | None
    subject_name: str | None
    processes: OverviewMetricResponse


class OverviewCollectionResponse(APIModel):
    collection_id: UUID
    job_id: UUID
    environment: Literal["demo", "real"]
    status: JobStatus
    created_at: datetime


class OverviewResponse(APIModel):
    generated_at: datetime
    data_source: Literal["synthetic", "datajud"]
    filters: OverviewFiltersResponse
    processes: OverviewMetricResponse
    representations: OverviewMetricResponse
    triage: OverviewTriageResponse
    followed_processes: OverviewMetricResponse
    pending_news: OverviewMetricResponse
    current_signals: list[OverviewSignalCategoryResponse]
    themes: list[OverviewThemeResponse]
    latest_collections: list[OverviewCollectionResponse]
    latest_observation_at: datetime | None


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
