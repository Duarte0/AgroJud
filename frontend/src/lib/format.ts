import type {
  EnvironmentName,
  JobKind,
  JobStatus,
  LatestCollection,
} from "@/api/types";

export const NOT_INFORMED = "Não informado";
export const DISPLAY_TIME_ZONE = "America/Sao_Paulo";

const dateTimeFormatter = new Intl.DateTimeFormat("pt-BR", {
  timeZone: DISPLAY_TIME_ZONE,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/** Formats an instant in America/Sao_Paulo; absent values are never replaced by "now". */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return NOT_INFORMED;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return dateTimeFormatter.format(parsed);
}

/** Formats a calendar date (YYYY-MM-DD) without shifting it through a time zone. */
export function formatCalendarDate(value: string | null | undefined): string {
  if (!value) return NOT_INFORMED;
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return value;
  return `${match[3]}/${match[2]}/${match[1]}`;
}

/** NNNNNNN-DD.AAAA.J.TR.OOOO for 20-digit CNJ numbers; other values are kept as received. */
export function formatCnj(value: string): string {
  const match = /^(\d{7})(\d{2})(\d{4})(\d)(\d{2})(\d{4})$/.exec(value);
  if (!match) return value;
  const [, sequence, digits, year, segment, court, unit] = match;
  return `${sequence}-${digits}.${year}.${segment}.${court}.${unit}`;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return NOT_INFORMED;
  return new Intl.NumberFormat("pt-BR").format(value);
}

const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  queued: "Na fila",
  running: "Em execução",
  retry_wait: "Aguardando nova tentativa",
  completed: "Concluído",
  partial: "Parcial",
  failed: "Falhou",
  cancelled: "Cancelado",
};

export function jobStatusLabel(status: JobStatus): string {
  return JOB_STATUS_LABELS[status];
}

export const JOB_STATUSES = Object.keys(JOB_STATUS_LABELS) as JobStatus[];

/** Attempt outcomes reuse job states when they match; unknown codes stay visible. */
export function attemptOutcomeLabel(outcome: string | null): string {
  if (outcome === null) return "Em andamento";
  return outcome in JOB_STATUS_LABELS ? JOB_STATUS_LABELS[outcome as JobStatus] : outcome;
}

const JOB_KIND_LABELS: Record<JobKind, string> = {
  discovery: "Descoberta por preset",
  refresh_number: "Atualização por número",
};

export function jobKindLabel(kind: JobKind): string {
  return JOB_KIND_LABELS[kind];
}

export const JOB_KINDS = Object.keys(JOB_KIND_LABELS) as JobKind[];

const REASON_LABELS: Record<string, string> = {
  limit: "Limite de registros da execução atingido",
  rejections: "Há registros rejeitados na validação",
  source_error: "Erro da fonte",
  persistence_error: "Erro ao gravar no banco local",
  retry_exhausted: "Tentativas esgotadas",
  persistence_retry_exhausted: "Tentativas de gravação esgotadas",
  cursor_invalid: "A fonte invalidou o cursor da varredura",
  capability_not_approved: "Capacidade da fonte não aprovada",
  source_cooldown: "Fonte em pausa temporária",
  source_retry: "Nova tentativa agendada após falha da fonte",
  recovery_exhausted: "Recuperações esgotadas sem progresso",
  cancel_requested: "Cancelamento solicitado",
};

export function reasonLabel(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return REASON_LABELS[reason] ?? reason;
}

const ENVIRONMENT_LABELS: Record<EnvironmentName, string> = {
  demo: "Demonstração — dados sintéticos",
  real: "Real — DataJud/TJGO",
  test: "Teste — banco isolado",
};

export function environmentLabel(environment: EnvironmentName): string {
  return ENVIRONMENT_LABELS[environment];
}

const CAPTURE_OUTCOME_LABELS: Record<LatestCollection["capture_outcome"], string> = {
  new: "Novo",
  updated: "Atualizado",
  unchanged: "Sem alteração",
};

export function captureOutcomeLabel(outcome: LatestCollection["capture_outcome"]): string {
  return CAPTURE_OUTCOME_LABELS[outcome];
}

const DATE_STATUS_LABELS: Record<string, string> = {
  timezone_aware: "Data com fuso informado",
  timezone_ambiguous: "Data ambígua: a fonte não informou o fuso",
  missing: NOT_INFORMED,
  null: NOT_INFORMED,
  unparseable: "Data ilegível na fonte",
};

export function dateStatusLabel(status: string): string {
  return DATE_STATUS_LABELS[status] ?? status;
}

const COMPARISON_LABELS: Record<string, string> = {
  FIRST_OBSERVED: "Primeira observação",
  KNOWN: "Já conhecido",
  ALTERATION_OBSERVED: "Alteração observada na fonte",
};

export function comparisonLabel(result: string): string {
  return COMPARISON_LABELS[result] ?? result;
}

const RURAL_LINK_LABELS: Record<string, string> = {
  not_confirmed: "Vínculo rural não confirmado",
  confirmed: "Vínculo rural confirmado",
};

export function ruralLinkLabel(status: string): string {
  return RURAL_LINK_LABELS[status] ?? status;
}
