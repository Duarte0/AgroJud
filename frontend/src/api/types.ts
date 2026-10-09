import type { components, paths } from "@/api-schema";

type Schemas = components["schemas"];

// Aliases only: every shape comes from the generated OpenAPI contract.
export type Environment = Schemas["EnvironmentResponse"];
export type EnvironmentName = Environment["environment"];
export type Preset = Schemas["PresetResponse"];
export type Presets = Schemas["PresetsResponse"];
export type SavedSearch = Schemas["SavedSearchResponse"];
export type SavedSearchCreate = Schemas["SavedSearchCreateRequest"];
export type SavedSearchPatch = Schemas["SavedSearchPatchRequest"];
export type DiscoveryCriteria = Schemas["DiscoveryCriteria"];
export type CreateJobRequest = Schemas["DiscoveryJobRequest"] | Schemas["RefreshNumberJobRequest"];
export type JobCreated = Schemas["JobCreatedResponse"];
export type JobCommandResult = Schemas["JobCommandResponse"];
export type JobSummary = Schemas["JobSummaryResponse"];
export type JobDetail = Schemas["JobDetailResponse"];
export type JobAttempt = Schemas["JobAttemptResponse"];
export type JobEvent = Schemas["JobEventResponse"];
export type JobStatus = JobSummary["status"];
export type JobKind = JobSummary["kind"];
export type JobPage = Schemas["PaginationResponse_JobSummaryResponse_"];
export type ProcessSummary = Schemas["ProcessSummaryResponse"];
export type ProcessDetail = Schemas["ProcessDetailResponse"];
export type ProcessPage = Schemas["PaginationResponse_ProcessSummaryResponse_"];
export type ProcessTriage = Schemas["ProcessTriageStateResponse"];
export type ProcessTriagePatch = Schemas["ProcessTriagePatchRequest"];
export type ProcessTriageHistoryEntry = Schemas["ProcessTriageHistoryEntryResponse"];
export type ProcessTriageHistoryPage =
  Schemas["PaginationResponse_ProcessTriageHistoryEntryResponse_"];
export type ProcessWatch = Schemas["ProcessWatchResponse"];
export type ProcessWatchHistory = Schemas["ProcessWatchHistoryResponse"];
export type ProcessRefreshResult = Schemas["ProcessRefreshResultResponse"];
export type WatchlistItem = Schemas["WatchlistItemResponse"];
export type WatchlistPage = Schemas["PaginationResponse_WatchlistItemResponse_"];
export type ProcessNews = Schemas["ProcessNewsResponse"];
export type ProcessNewsPage = Schemas["PaginationResponse_ProcessNewsResponse_"];
export type NewsCategory = ProcessNews["category"];
export type NewsStatus = ProcessNews["status"];
export type ProcessNewsStatusPatch = Schemas["ProcessNewsStatusPatchRequest"];
export type NewsListQuery = NonNullable<
  paths["/api/v1/news"]["get"]["parameters"]["query"]
>;
export type SignalRunRequest = Schemas["SignalRunRequest"];
export type SignalRunAccepted = Schemas["SignalRunAcceptedResponse"];
export type SignalRunStatus = Schemas["SignalRunStatusResponse"];
export type ProcessSignal = Schemas["ProcessSignalResponse"];
export type ProcessSignals = Schemas["ProcessSignalsResponse"];
export type TriageDecision = ProcessTriage["decision"];
export type RuralLink = ProcessTriage["rural_link"];
export type LatestCollection = Schemas["LatestCollectionResponse"];
export type Representation = Schemas["ProcessRepresentationResponse"];
export type RepresentationPage = Schemas["PaginationResponse_ProcessRepresentationResponse_"];
export type Movement = Schemas["ProcessMovementResponse"];
export type MovementPage = Schemas["ProcessMovementsResponse"];
export type MovementDiagnostic = Schemas["MovementDiagnosticResponse"];
export type ErrorBody = Schemas["ErrorBody"];

export type JobListQuery = NonNullable<
  paths["/api/v1/jobs"]["get"]["parameters"]["query"]
>;
export type ProcessListQuery = NonNullable<
  paths["/api/v1/processes"]["get"]["parameters"]["query"]
>;
export type WatchlistQuery = NonNullable<
  paths["/api/v1/watchlist"]["get"]["parameters"]["query"]
>;
export type JobCommand = "cancel" | "resume" | "continue" | "restart-scan";
