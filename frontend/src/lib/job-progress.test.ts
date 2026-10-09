import type { JobSummary } from "@/api/types";
import { buildJob } from "@/test/factories";
import {
  JOB_POLL_INTERVAL_MS,
  jobListPollInterval,
  jobPollInterval,
  lastActivityAt,
  readJobProgress,
} from "@/lib/job-progress";

describe("polling", () => {
  it.each(["queued", "running", "retry_wait"] as const)("polls %s jobs every 3s", (status) => {
    expect(jobPollInterval(status)).toBe(JOB_POLL_INTERVAL_MS);
    expect(JOB_POLL_INTERVAL_MS).toBe(3_000);
  });

  it.each(["completed", "partial", "failed", "cancelled"] as const)(
    "stops polling %s jobs",
    (status) => {
      expect(jobPollInterval(status)).toBe(false);
    },
  );

  it("stops polling before the first answer and for lists without active jobs", () => {
    expect(jobPollInterval(undefined)).toBe(false);
    expect(jobListPollInterval([{ status: "completed" } as JobSummary])).toBe(false);
    expect(
      jobListPollInterval([{ status: "completed" }, { status: "running" }] as JobSummary[]),
    ).toBe(JOB_POLL_INTERVAL_MS);
  });
});

describe("readJobProgress", () => {
  it("computes a percentage only for exact remote totals", () => {
    const exact = readJobProgress({
      hits_confirmed: 50,
      source_total: { present: true, value: 200, relation: "eq" },
    });
    expect(exact.sourceTotal).toEqual({ kind: "exact", value: 200 });
    expect(exact.percent).toBe(25);

    const lowerBound = readJobProgress({
      hits_confirmed: 50,
      source_total: { present: true, value: 10_000, relation: "gte" },
    });
    expect(lowerBound.sourceTotal.kind).toBe("lower_bound");
    expect(lowerBound.percent).toBeNull();

    const absent = readJobProgress({
      hits_confirmed: 50,
      source_total: { present: false, value: null, relation: null },
    });
    expect(absent.sourceTotal.kind).toBe("unknown");
    expect(absent.percent).toBeNull();
  });

  it("keeps unknown counters as null instead of inventing zero", () => {
    const progress = readJobProgress(null);
    expect(progress.hitsConfirmed).toBeNull();
    expect(progress.coverage).toBeNull();
    expect(progress.hasPersistedData).toBeNull();
  });
});

describe("lastActivityAt", () => {
  it("returns the latest persisted timestamp", () => {
    const job = buildJob({
      events: [
        { event_number: 1, event_type: "created", details: {}, created_at: "2026-10-09T10:00:00Z" },
        { event_number: 2, event_type: "page_committed", details: {}, created_at: "2026-10-09T10:05:00Z" },
      ],
    });
    expect(lastActivityAt(job)).toBe("2026-10-09T10:05:00Z");
  });
});
