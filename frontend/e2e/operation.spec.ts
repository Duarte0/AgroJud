import { expect, test } from "@playwright/test";

import {
  collectionResultCount,
  demoDatabaseSizeBytes,
  evidence,
  formatProcessNumber,
  demoDatabaseVersion,
  expectJobStatus,
  measurementMachine,
  restartApi,
  setProcessNumber,
  startCollection,
  startWorker,
  stopWorker,
  uniqueProcessNumber,
} from "./support";

test.describe.configure({ mode: "serial" });

test.afterEach(() => {
  startWorker();
});

test("jornada sintética integrada: reinício, coleta, revisão, acompanhamento, atualização e CSV", async ({
  page,
}) => {
  const databaseBytesBefore = demoDatabaseSizeBytes();
  stopWorker();
  const jobId = await startCollection(page);
  await expectJobStatus(page, "Na fila");

  restartApi();
  await page.reload();
  await expectJobStatus(page, "Na fila");
  startWorker();
  await expectJobStatus(page, "Concluído");

  const jobResponse = await page.request.get(new URL(`/api/v1/jobs/${jobId}`, page.url()).toString());
  expect(jobResponse.ok()).toBeTruthy();
  const job = (await jobResponse.json()) as {
    collection_id: string;
    environment: string;
    source: string;
    started_at: string;
    finished_at: string;
    retry_cycle: number;
    checkpoint: { next_page: number };
  };
  expect(job.environment).toBe("demo");
  expect(job.source).toBe("synthetic");

  const resultCount = collectionResultCount(job.collection_id);
  const databaseBytesAfter = demoDatabaseSizeBytes();
  const measurement = {
    machine: measurementMachine(),
    postgres_version: demoDatabaseVersion(),
    source: "synthetic",
    processes_or_hits: resultCount,
    pages_requested: job.checkpoint.next_page - 1,
    retries: job.retry_cycle - 1,
    duration_ms: Date.parse(job.finished_at) - Date.parse(job.started_at),
    database_bytes_before_collection: databaseBytesBefore,
    database_bytes_after_collection: databaseBytesAfter,
  };
  expect(measurement.processes_or_hits).toBeGreaterThan(0);
  expect(measurement.pages_requested).toBeGreaterThan(0);
  expect(measurement.retries).toBe(0);
  expect(measurement.duration_ms).toBeGreaterThanOrEqual(0);
  console.log(`[SPEC-020-MEASUREMENT] ${JSON.stringify(measurement)}`);

  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await expect(page).toHaveURL(/\/processes\?collection_id=/);
  await page.getByRole("link", { name: "0000001-00.2026.8.09.0001" }).click();
  const processId = page.url().split("/").pop()!;
  const processNumber = uniqueProcessNumber();
  const displayedProcessNumber = formatProcessNumber(processNumber);
  setProcessNumber(processId, processNumber);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(displayedProcessNumber);

  await page.getByLabel("Decisão", { exact: true }).selectOption("relevant");
  await page.getByLabel("Vínculo rural").selectOption("confirmed");
  await page.getByLabel("Nota de revisão").fill("Aceite sintético da SPEC-020.");
  await page.getByRole("button", { name: "Salvar triagem" }).click();
  await expect(page.getByText(/Versão 1 · atualizada/)).toBeVisible();

  await page.getByRole("button", { name: "Acompanhar processo" }).click();
  await expect(page.getByText("Acompanhamento ativo")).toBeVisible();
  await page.getByRole("button", { name: "Atualizar processo" }).click();
  await page.waitForURL(/\/jobs\/[0-9a-f-]{36}$/);
  await expectJobStatus(page, "Concluído");

  await page.goto("/watchlist");
  const watched = page.getByTestId("watchlist-entry").filter({
    hasText: displayedProcessNumber,
  });
  await expect(watched).toContainText("Encontrado na consulta");
  await evidence(page, "spec020-acompanhamento-atualizado");

  await page.goto(`/processes?collection_id=${job.collection_id}`);
  await expect(page.getByRole("heading", { name: "Processos" })).toBeVisible();
  await evidence(page, "spec020-processos-para-exportacao");
  const downloadReady = page.waitForEvent("download");
  await page.getByRole("button", { name: "Exportar CSV" }).click();
  const download = await downloadReady;
  expect(download.suggestedFilename()).toBe("agrojud-processos-demo.csv");

  await expect(page.getByRole("region", { name: "Ambiente" })).toContainText("Demonstração");

  await page.goto(`/processes/${processId}`);
  await page.getByRole("button", { name: "Remover acompanhamento" }).click();
  await expect(page.getByText("Não acompanhado")).toBeVisible();
});
