import type { JobDetail, JobStatus, JobSummary } from "@/api/types";

export const ACTIVE_JOB_STATUSES: ReadonlySet<JobStatus> = new Set([
  "queued",
  "running",
  "retry_wait",
]);
export const JOB_POLL_INTERVAL_MS = 3_000;

export function isActiveJob(status: JobStatus | undefined): boolean {
  return status !== undefined && ACTIVE_JOB_STATUSES.has(status);
}

/** Polls only while the persisted status may still change. */
export function jobPollInterval(status: JobStatus | undefined): number | false {
  return isActiveJob(status) ? JOB_POLL_INTERVAL_MS : false;
}

export function jobListPollInterval(items: readonly JobSummary[] | undefined): number | false {
  return items?.some((job) => isActiveJob(job.status)) ? JOB_POLL_INTERVAL_MS : false;
}

export type SourceTotal =
  | { kind: "exact"; value: number }
  | { kind: "lower_bound"; value: number; relation: string }
  | { kind: "unknown" };

export type JobProgress = {
  pagesConfirmed: number | null;
  hitsConfirmed: number | null;
  validHits: number | null;
  rejectedHits: number | null;
  quarantineRecords: number | null;
  newCount: number | null;
  updatedCount: number | null;
  unchangedCount: number | null;
  budgetLimit: number | null;
  sourceTotal: SourceTotal;
  /** Only present when the remote total is exact; never estimated. */
  percent: number | null;
  coverage: "complete" | "partial" | null;
  queryStatus: string | null;
  hasPersistedData: boolean | null;
};

function count(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function readSourceTotal(value: unknown): SourceTotal {
  if (!value || typeof value !== "object") return { kind: "unknown" };
  const total = value as Record<string, unknown>;
  const amount = total.value;
  if (total.present !== true || typeof amount !== "number" || amount < 0) {
    return { kind: "unknown" };
  }
  if (total.relation === "eq") return { kind: "exact", value: amount };
  return {
    kind: "lower_bound",
    value: amount,
    relation: typeof total.relation === "string" ? total.relation : "desconhecida",
  };
}

export function readJobProgress(coverage: JobDetail["coverage"]): JobProgress {
  const record: Record<string, unknown> = coverage ?? {};
  const hitsConfirmed = count(record, "hits_confirmed");
  const sourceTotal = readSourceTotal(record.source_total);
  let percent: number | null = null;
  if (sourceTotal.kind === "exact" && hitsConfirmed !== null) {
    percent =
      sourceTotal.value === 0
        ? 100
        : Math.min(100, Math.floor((hitsConfirmed / sourceTotal.value) * 100));
  }
  const coverageValue = record.coverage;
  return {
    pagesConfirmed: count(record, "pages_confirmed"),
    hitsConfirmed,
    validHits: count(record, "valid_hits"),
    rejectedHits: count(record, "rejected_hits"),
    quarantineRecords: count(record, "quarantine_records"),
    newCount: count(record, "new"),
    updatedCount: count(record, "updated"),
    unchangedCount: count(record, "unchanged"),
    budgetLimit: count(record, "budget_limit"),
    sourceTotal,
    percent,
    coverage:
      coverageValue === "complete" || coverageValue === "partial" ? coverageValue : null,
    queryStatus: typeof record.query_status === "string" ? record.query_status : null,
    hasPersistedData:
      typeof record.has_persisted_data === "boolean" ? record.has_persisted_data : null,
  };
}

/** Latest persisted timestamp known for the job; null when nothing was recorded. */
export function lastActivityAt(job: JobDetail): string | null {
  const candidates = [
    job.created_at,
    job.started_at,
    job.finished_at,
    job.checkpoint.updated_at,
    ...job.events.map((event) => event.created_at),
    ...job.attempts.flatMap((attempt) => [attempt.started_at, attempt.finished_at]),
  ].filter((value): value is string => Boolean(value));
  let latest: string | null = null;
  let latestTime = -Infinity;
  for (const value of candidates) {
    const time = Date.parse(value);
    if (!Number.isNaN(time) && time > latestTime) {
      latest = value;
      latestTime = time;
    }
  }
  return latest;
}
