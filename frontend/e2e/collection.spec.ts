import { expect, test } from "@playwright/test";

import {
  evidence,
  expectJobStatus,
  startCollection,
  startWorker,
  stopWorker,
  trackApiRequests,
} from "./support";

const JOB_DETAIL = /^\/api\/v1\/jobs\/[0-9a-f-]{36}$/;

test.describe.configure({ mode: "serial" });

test.afterEach(() => {
  // Every test leaves the worker running for the next one.
  startWorker();
});

test("AC1/AC4: inicia, acompanha e abre o processo sem envio duplicado", async ({ page }) => {
  const requests = trackApiRequests(page);
  await page.goto("/");
  await expect(page).toHaveURL(/\/radar$/);
  await expect(page.getByRole("region", { name: "Ambiente" })).toContainText(
    "Demonstração — dados sintéticos",
  );
  await evidence(page, "01-radar");

  const jobId = await startCollection(page, { doubleClick: true });
  expect(requests.count("POST", /^\/api\/v1\/jobs$/)).toBe(1);

  await expectJobStatus(page, "Concluído");
  await expect(page.getByTestId("polling-status")).toContainText("Atualização automática encerrada");
  await expect(page.getByTestId("coverage")).toHaveText("Completa para a consulta");
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  await evidence(page, "02-job-concluido");

  // Polling stops at the terminal state: no further detail reads for > 2 intervals.
  const readsAtTerminal = requests.count("GET", JOB_DETAIL);
  await page.waitForTimeout(7_000);
  expect(requests.count("GET", JOB_DETAIL)).toBe(readsAtTerminal);
  expect(requests.count("POST", /^\/api\/v1\/jobs/)).toBe(1);

  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await expect(page).toHaveURL(/\/processes\?collection_id=/);
  await expect(page.getByLabel("Coleta (ID)")).not.toHaveValue("");
  const rows = page.getByRole("table").getByRole("row");
  await expect(rows).toHaveCount(2);
  await evidence(page, "03-processos-da-coleta");

  await page.getByRole("link", { name: "0000001-00.2026.8.09.0001" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("0000001-00.2026.8.09.0001");
  await expect(page.getByTestId("representation").first()).toContainText("Classe TPU");
  const movement = page.getByTestId("movement").first();
  await expect(movement).toContainText("Data do evento");
  await expect(movement).toContainText("Atualização da fonte");
  await expect(movement).toContainText("Observação local");
  await expect(movement).toContainText("Origem: TJGO");
  await evidence(page, "04-processo-timeline");
  expect(jobId).toMatch(/[0-9a-f-]{36}/);
});

test("cancelamento interrompe o polling e retomada volta a acompanhar", async ({ page }) => {
  stopWorker();
  const requests = trackApiRequests(page);
  const jobId = await startCollection(page);
  await expectJobStatus(page, "Na fila");
  await expect(page.getByTestId("polling-status")).toContainText("a cada 3 segundos");

  await expect(page.getByText("Cancelar não desfaz resultados de páginas já confirmadas.")).toBeVisible();
  await page.getByRole("button", { name: "Cancelar coleta" }).click();
  await expectJobStatus(page, "Cancelado");
  await expect(page.getByTestId("command-confirmed")).toContainText("Cancelado");
  await expect(page.getByText("O cancelamento não desfaz resultados confirmados antes dele.")).toBeVisible();
  await evidence(page, "05-job-cancelado");

  const detail = new RegExp(`^/api/v1/jobs/${jobId}$`);
  const readsAfterCancel = requests.count("GET", detail);
  await page.waitForTimeout(7_000);
  expect(requests.count("GET", detail)).toBe(readsAfterCancel);

  await page.getByRole("button", { name: "Retomar coleta" }).click();
  await expectJobStatus(page, "Na fila");
  const readsAfterResume = requests.count("GET", detail);
  await page.waitForTimeout(6_500);
  expect(requests.count("GET", detail)).toBeGreaterThanOrEqual(readsAfterResume + 2);
  expect(requests.count("POST", /\/cancel$/)).toBe(1);
  expect(requests.count("POST", /\/resume$/)).toBe(1);
  expect(requests.count("POST", /^\/api\/v1\/jobs$/)).toBe(1);

  startWorker();
  await expectJobStatus(page, "Concluído");
});

test("resultado parcial por limite pode ser continuado", async ({ page }) => {
  const requests = trackApiRequests(page);
  await startCollection(page, { hitBudget: 1 });
  await expectJobStatus(page, "Parcial");
  await expect(page.getByText("Resultado parcial: Limite de registros da execução atingido")).toBeVisible();
  await expect(page.getByTestId("hits-confirmed")).toHaveText("1 de limite 1");
  await evidence(page, "06-job-parcial");

  await page.getByRole("button", { name: "Continuar coleta" }).click();
  await expectJobStatus(page, "Concluído");
  expect(requests.count("POST", /\/continue$/)).toBe(1);
});
