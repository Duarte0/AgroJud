import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { evidence, expectJobStatus, startCollection } from "./support";

test("SPEC-019 baixa todos os resultados filtrados sem aplicar a página atual", async ({ page }) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(message.text());
  });

  const jobId = await startCollection(page);
  await expectJobStatus(page, "Concluído");

  const jobResponse = await page.request.get(
    new URL(`/api/v1/jobs/${jobId}`, page.url()).toString(),
  );
  expect(jobResponse.status()).toBe(200);
  const job = (await jobResponse.json()) as { collection_id: string };
  const collectionId = job.collection_id;

  const listResponse = await page.request.get(
    new URL(`/api/v1/processes?collection_id=${collectionId}`, page.url()).toString(),
  );
  expect(listResponse.status()).toBe(200);
  const processList = (await listResponse.json()) as {
    items: Array<{ numero_cnj: string }>;
    total: number;
  };
  expect(processList.total).toBeGreaterThan(0);

  await page.goto(`/processes?collection_id=${collectionId}&page=999`);
  await expect(page).toHaveTitle("Processos · AgroJud Radar");
  await expect(page.getByRole("heading", { name: "Processos" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: /Mais filtros/ }).click();
  await expect(page.getByRole("combobox", { name: "Coleta" })).toHaveValue(/rural\.credito_contratos · Concluída/);
  await expect(page.getByRole("combobox", { name: "Coleta" })).not.toHaveValue(collectionId);
  await expect(page.getByText("Esta página não contém processos")).toBeVisible();
  await evidence(page, "spec019-exportacao-filtrada");

  let exportUrl: URL | undefined;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/exports/processes.csv") exportUrl = url;
  });
  const downloadReady = page.waitForEvent("download");
  await page.getByRole("button", { name: "Exportar CSV" }).click();
  const download = await downloadReady;
  expect(download.suggestedFilename()).toBe("agrojud-processos-demo.csv");

  const filePath = await download.path();
  expect(filePath).not.toBeNull();
  const bytes = readFileSync(filePath!);
  expect(bytes.subarray(0, 3)).toEqual(Buffer.from([0xef, 0xbb, 0xbf]));
  const csv = bytes.toString("utf8").replace(/^\uFEFF/, "");
  const lines = csv.split("\r\n");
  expect(lines[0]).toBe(
    "numero_cnj;tribunal;classes;graus;orgaos;assuntos;triagem;vinculo_rural;" +
      "motivos_captura;acompanhado;primeira_observacao;ultima_observacao;data_source;exportado_em",
  );
  const exportedNumbers = lines
    .slice(1)
    .filter(Boolean)
    .map((line) => line.split(";", 1)[0]!);
  expect(exportedNumbers.sort()).toEqual(
    processList.items.map((process) => process.numero_cnj).sort(),
  );
  expect(exportUrl?.searchParams.get("collection_id")).toBe(collectionId);
  expect(exportUrl?.searchParams.has("page")).toBe(false);
  expect(exportUrl?.searchParams.has("page_size")).toBe(false);
  expect(runtimeErrors).toEqual([]);
});
