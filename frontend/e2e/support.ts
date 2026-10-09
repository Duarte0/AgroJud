import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
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
