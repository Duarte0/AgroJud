import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { expect, type Page, type Request } from "@playwright/test";

const repositoryRoot = path.resolve(import.meta.dirname, "../..");
const evidenceDir = process.env.E2E_EVIDENCE_DIR;

function compose(...args: string[]): string {
  const envFile = process.env.E2E_ENV_FILE;
  const project = process.env.E2E_COMPOSE_PROJECT;
  if (!envFile || !project?.includes("e2e")) {
    throw new Error("E2E_ENV_FILE/E2E_COMPOSE_PROJECT ausentes; execute via scripts/e2e.sh.");
  }
  return execFileSync(
    "docker",
    [
      "compose",
      "--project-name",
      project,
      "--env-file",
      envFile,
      "-f",
      path.join(repositoryRoot, "compose.yaml"),
      "-f",
      path.join(repositoryRoot, "compose.e2e.yaml"),
      "--profile",
      "worker",
      ...args,
    ],
    { encoding: "utf8" },
  );
}

/** Pauses job processing so tests can observe queued jobs deterministically. */
export function stopWorker() {
  compose("stop", "worker");
}

export function startWorker() {
  compose("up", "--detach", "worker");
}

/** Restarts only the API while preserving the isolated E2E database and pending jobs. */
export function restartApi() {
  compose("restart", "api");
  compose("up", "--detach", "--wait", "api");
}

function queryDemoDatabase(sql: string): string {
  return compose(
    "exec",
    "-T",
    "--env",
    `SPEC020_SQL=${sql}`,
    "db",
    "sh",
    "-ec",
    'psql -X --set=ON_ERROR_STOP=1 --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command "$SPEC020_SQL"',
  ).trim();
}

export function demoDatabaseSizeBytes(): number {
  const value = queryDemoDatabase("SELECT pg_database_size(current_database())");
  if (!/^\d+$/.test(value)) throw new Error("PostgreSQL did not return a database size.");
  return Number(value);
}

export function demoDatabaseVersion(): string {
  return queryDemoDatabase("SELECT current_setting('server_version')");
}

export function measurementMachine() {
  return {
    platform: process.platform,
    architecture: process.arch,
    cpu_count: os.cpus().length,
    host_memory_bytes: os.totalmem(),
    node_version: process.version,
    docker_engine: execFileSync("docker", ["version", "--format", "{{.Server.Version}}"], {
      encoding: "utf8",
    }).trim(),
    compose_version: execFileSync("docker", ["compose", "version", "--short"], {
      encoding: "utf8",
    }).trim(),
    container_cpu_or_memory_limits: "not configured",
  };
}

export function collectionResultCount(collectionId: string): number {
  if (!/^[0-9a-f-]{36}$/i.test(collectionId)) {
    throw new Error("Collection ID inválido para a medição sintética.");
  }
  const value = queryDemoDatabase(
    `SELECT count(*) FROM collection_results WHERE collection_id = '${collectionId}'::uuid`,
  );
  if (!/^\d+$/.test(value)) throw new Error("PostgreSQL did not return a result count.");
  return Number(value);
}

/** Adjusts only the disposable synthetic E2E row so the local rule has evidence. */
export function setProcessMovementCode(processId: string, code: number): string {
  if (!/^[0-9a-f-]{36}$/i.test(processId) || !Number.isSafeInteger(code) || code <= 0) {
    throw new Error("Process ID ou código TPU inválido para fixture E2E.");
  }
  const sql = `UPDATE movement_occurrences AS occurrence
    SET normalized_content = jsonb_set(
      jsonb_set(occurrence.normalized_content, '{codigo}', to_jsonb(${code})),
      '{nome}', to_jsonb('Bloqueio, Penhora ou Arresto'::text)
    )
    FROM representations AS representation
    WHERE representation.id = occurrence.representation_id
      AND representation.process_id = '${processId}'::uuid
      AND representation.id = (
        SELECT selected.id
        FROM representations AS selected
        WHERE selected.process_id = '${processId}'::uuid
        ORDER BY selected.last_observed_at DESC, selected.id DESC
        LIMIT 1
      )
    RETURNING occurrence.id`;
  const occurrenceId = compose(
    "exec",
    "-T",
    "--env",
    `SIGNAL_SEED_SQL=${sql}`,
    "db",
    "sh",
    "-lc",
    'psql --quiet --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command "$SIGNAL_SEED_SQL"',
  ).trim();
  if (!/^[0-9a-f-]{36}$/i.test(occurrenceId)) {
    throw new Error("A fixture E2E não encontrou uma ocorrência única para o processo.");
  }
  return occurrenceId;
}

/** Seeds a disposable pending baseline and dated novelty for browser acceptance. */
export function seedPendingNews(processId: string): string {
  if (!/^[0-9a-f-]{36}$/i.test(processId)) {
    throw new Error("Process ID inválido para fixture E2E de novidades.");
  }
  const sql = `WITH target AS MATERIALIZED (
    SELECT cycle.id AS watch_cycle_id, process.id AS process_id,
           representation.id AS representation_id, representation.latest_version_id AS version_id,
           snapshot.id AS snapshot_id, occurrence.id AS occurrence_id,
           occurrence.normalized_content
    FROM process_watch_cycles AS cycle
    JOIN process_watchlist_entries AS entry ON entry.process_id = cycle.process_id AND entry.active
    JOIN processes AS process ON process.id = cycle.process_id
    JOIN representation_watch_baselines AS baseline ON baseline.watch_cycle_id = cycle.id
    JOIN representations AS representation ON representation.id = baseline.representation_id
    JOIN movement_snapshots AS snapshot ON snapshot.id = baseline.snapshot_id
    JOIN movement_snapshot_occurrences AS present
      ON present.snapshot_id = snapshot.id AND present.present
    JOIN movement_occurrences AS occurrence ON occurrence.id = present.occurrence_id
    WHERE process.id = '${processId}'::uuid AND cycle.ended_at IS NULL
    ORDER BY representation.id, occurrence.id
    LIMIT 1
  ), reset AS (
    UPDATE representation_watch_baselines AS baseline
    SET state = 'pending', version_id = NULL, snapshot_id = NULL,
        normalizer_version = NULL, established_at = NULL
    FROM target
    WHERE baseline.watch_cycle_id = target.watch_cycle_id
      AND baseline.representation_id = target.representation_id
    RETURNING baseline.id
  )
  INSERT INTO process_news (
    id, process_id, representation_id, watch_cycle_id, category, status, identity_key,
    occurrence_id, origin_version_id, origin_snapshot_id, source_date,
    source_date_original, source_date_status, first_observed_at, evidence, provenance
  )
  SELECT gen_random_uuid(), target.process_id, target.representation_id,
         target.watch_cycle_id, 'NEW_OBSERVATION', 'pending',
         'e2e-news:' || target.occurrence_id::text, target.occurrence_id,
         target.version_id, target.snapshot_id, '2020-04-01T03:04:05Z'::timestamptz,
         to_jsonb('2020-04-01T03:04:05Z'::text), 'timezone_aware',
         '2026-10-09T12:00:00Z'::timestamptz,
         jsonb_build_object('content', target.normalized_content, 'evidence', 'synthetic E2E'),
         'ingestion'
  FROM target CROSS JOIN reset
  ON CONFLICT DO NOTHING
  RETURNING id`;
  const newsId = compose(
    "exec",
    "-T",
    "--env",
    `NEWS_SEED_SQL=${sql}`,
    "db",
    "sh",
    "-lc",
    'psql --quiet --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command "$NEWS_SEED_SQL"',
  ).trim();
  if (!/^[0-9a-f-]{36}$/i.test(newsId)) {
    throw new Error("A fixture E2E não conseguiu preparar baseline e novidade.");
  }
  return newsId;
}

/** Creates a unique, structurally valid CNJ for a disposable browser scenario. */
export function uniqueProcessNumber(): string {
  const sequence = String(Date.now() % 10_000_000).padStart(7, "0");
  const bodyWithoutCheckDigits = `${sequence}20268090001`;
  let remainder = 0;
  for (const digit of `${bodyWithoutCheckDigits}00`) {
    remainder = (remainder * 10 + Number(digit)) % 97;
  }
  return `${sequence}${String(98 - remainder).padStart(2, "0")}20268090001`;
}

export function formatProcessNumber(digits: string): string {
  if (!/^\d{20}$/.test(digits)) throw new Error("CNJ inválido para formatação E2E.");
  return digits.replace(/^(\d{7})(\d{2})(\d{4})(\d)(\d{2})(\d{4})$/, "$1-$2.$3.$4.$5.$6");
}

/** Isolates a browser scenario from shared synthetic CNJs. */
export function setProcessNumber(processId: string, processNumber: string): void {
  if (!/^[0-9a-f-]{36}$/i.test(processId) || !/^\d{20}$/.test(processNumber)) {
    throw new Error("Process ID ou número CNJ inválido para isolamento E2E.");
  }
  const updatedId = compose(
    "exec",
    "-T",
    "--env",
    `PROCESS_NUMBER_SQL=UPDATE processes SET numero_cnj = '${processNumber}' WHERE id = '${processId}'::uuid RETURNING id`,
    "db",
    "sh",
    "-lc",
    'psql --quiet --tuples-only --no-align --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --command "$PROCESS_NUMBER_SQL"',
  ).trim();
  if (updatedId.toLowerCase() !== processId.toLowerCase()) {
    throw new Error("A fixture E2E não conseguiu isolar o processo.");
  }
}

/** A filing window no other test uses, so each collection is a distinct query. */
export function uniqueWindow(): { from: string; through: string } {
  const start = Date.UTC(2001, 0, 1) + Math.floor(Math.random() * 7_000) * 86_400_000;
  const from = new Date(start).toISOString().slice(0, 10);
  const through = new Date(start + 3 * 86_400_000).toISOString().slice(0, 10);
  return { from, through };
}

export type RequestLog = {
  count: (method: string, pattern: RegExp) => number;
};

export function trackApiRequests(page: Page): RequestLog {
  const requests: Request[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request);
  });
  return {
    count: (method, pattern) =>
      requests.filter(
        (request) => request.method() === method && pattern.test(new URL(request.url()).pathname),
      ).length,
  };
}

/** Starts a discovery from the radar with a unique window and returns the job id. */
export async function startCollection(
  page: Page,
  { hitBudget = 2000, doubleClick = false }: { hitBudget?: number; doubleClick?: boolean } = {},
): Promise<string> {
  await page.goto("/radar");
  await expect(page.getByRole("radio", { name: "Crédito e contratos rurais" })).toBeChecked();
  const window = uniqueWindow();
  await page.getByLabel(/Usar a janela padrão/).uncheck();
  await page.getByLabel("Ajuizados a partir de").fill(window.from);
  await page.getByLabel("Ajuizados até (inclusive)").fill(window.through);
  await page.getByLabel("Limite de registros desta execução").fill(String(hitBudget));
  const submit = page.getByRole("button", { name: "Iniciar coleta" });
  if (doubleClick) await submit.dblclick();
  else await submit.click();
  await page.waitForURL(/\/jobs\/[0-9a-f-]{36}$/);
  return page.url().split("/").pop()!;
}

export async function expectJobStatus(page: Page, label: string) {
  await expect(page.getByRole("heading", { level: 1 }).getByText(label, { exact: true })).toBeVisible({
    timeout: 30_000,
  });
}

export async function evidence(page: Page, name: string) {
  if (!evidenceDir) return;
  mkdirSync(evidenceDir, { recursive: true });
  const target = path.join(evidenceDir, `${name}.png`);
  try {
    await page.screenshot({ path: target, fullPage: true });
  } catch {
    // Chromium occasionally refuses one capture mid-render; a second failure is reported.
    await page.waitForTimeout(500);
    await page.screenshot({ path: target, fullPage: true });
  }
}

export async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
}
