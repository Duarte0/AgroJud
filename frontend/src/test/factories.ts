import type {
  Environment,
  JobDetail,
  Preset,
  ProcessSummary,
} from "@/api/types";

export const DEMO_ENVIRONMENT: Environment = {
  environment: "demo",
  source: "synthetic",
  source_enabled: true,
  source_disabled_reason: null,
};

export function buildJob(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    collection_id: "22222222-2222-4222-8222-222222222222",
    kind: "discovery",
    environment: "demo",
    source: "synthetic",
    tribunal: "TJGO",
    status: "queued",
    coverage: null,
    reason: null,
    cancel_requested: false,
    attempt_count: 0,
    retry_cycle: 0,
    next_attempt_at: "2026-10-09T10:00:00Z",
    created_at: "2026-10-09T10:00:00Z",
    started_at: null,
    finished_at: null,
    criteria: { preset_id: "rural.credito_contratos", preset_version: "1.0.0" },
    execution: {},
    checkpoint: { next_page: 1, revision: 0, updated_at: "2026-10-09T10:00:00Z" },
    cursor_invalid: false,
    predecessor_job_id: null,
    attempts: [],
    events: [],
    ...overrides,
  };
}

export function buildPreset(overrides: Partial<Preset> = {}): Preset {
  return {
    id: "rural.credito_contratos",
    version: "1.0.0",
    family: "rural",
    name: "Crédito e contratos rurais",
    purpose: "Demonstração",
    justification: "Teste",
    filters: { class: [], subject: [10501], movement: [] },
    include_descendants: false,
    rural_link_status: "not_confirmed",
    capture_explanation: "Capturado por assunto TPU.",
    evidence: {},
    required_for_real: [],
    availability: {
      environment: "demo",
      enabled: true,
      label: "demonstrativo sintético",
      reasons: ["Demonstração sintética explícita; isto não valida o TJGO."],
    },
    ...overrides,
  };
}

export function buildProcess(overrides: Partial<ProcessSummary> = {}): ProcessSummary {
  return {
    id: "33333333-3333-4333-8333-333333333333",
    numero_cnj: "00000010020268090001",
    created_at: "2026-10-09T10:00:00Z",
    representation_count: 1,
    latest_observed_at: "2026-10-09T10:00:00Z",
    latest_collection: null,
    ...overrides,
  };
}
