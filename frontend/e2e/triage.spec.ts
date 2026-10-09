import { expect, test } from "@playwright/test";

import { evidence, expectJobStatus, startCollection, startWorker } from "./support";

test.describe.configure({ mode: "serial" });

test.afterEach(() => {
  startWorker();
});

test("AC1-AC5: revisa, reverte, consulta histórico e preserva rascunho em conflito", async ({
  page,
}) => {
  await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await page.getByRole("link", { name: "0000001-00.2026.8.09.0001" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("0000001-00.2026.8.09.0001");
  await expect(page.getByText("Versão 0 · ainda não revisada")).toBeVisible();

  const processId = page.url().split("/").pop()!;
  const concurrentEdit = await page.request.patch(
    new URL(`/api/v1/processes/${processId}/triage`, page.url()).toString(),
    { data: { expected_version: 0, decision: "relevant", note: "Nota salva na outra aba." } },
  );
  expect(concurrentEdit.status()).toBe(200);

  await page.getByLabel("Decisão", { exact: true }).selectOption("discarded");
  await page.getByLabel("Nota de revisão").fill("Rascunho que deve permanecer após o conflito.");
  await page.getByRole("button", { name: "Salvar triagem" }).click();
  await expect(page.getByRole("alert")).toContainText("mudou em outra edição");
  await expect(page.getByLabel("Nota de revisão")).toHaveValue(
    "Rascunho que deve permanecer após o conflito.",
  );

  await page.getByRole("button", { name: "Recarregar dados" }).click();
  await expect(page.getByText(/Confira a versão 1/)).toBeVisible();
  await expect(page.getByLabel("Decisão", { exact: true })).toHaveValue("discarded");
  await expect(page.getByLabel("Nota de revisão")).toHaveValue(
    "Rascunho que deve permanecer após o conflito.",
  );
  await page.getByRole("button", { name: "Salvar triagem" }).click();
  await expect(page.getByText(/Versão 2 · atualizada/)).toBeVisible();

  await page.getByLabel("Decisão", { exact: true }).selectOption("relevant");
  await page.getByLabel("Vínculo rural").selectOption("confirmed");
  await page.getByLabel("Nota de revisão").fill("Vínculo confirmado em revisão manual.");
  await page.getByRole("button", { name: "Salvar triagem" }).click();
  await expect(page.getByText(/Versão 3 · atualizada/)).toBeVisible();

  await page.getByLabel("Decisão", { exact: true }).selectOption("pending");
  await page.getByLabel("Vínculo rural").selectOption("unconfirmed");
  await page.getByLabel("Nota de revisão").fill("");
  await page.getByRole("button", { name: "Salvar triagem" }).click();
  await expect(page.getByText(/Versão 4 · atualizada/)).toBeVisible();

  const entries = page.getByTestId("triage-history-entry");
  await expect(entries).toHaveCount(4);
  await expect(entries.first()).toContainText("Versão 4 · origem manual");
  await expect(entries.first()).toContainText("Vínculo rural:");
  await expect(entries.nth(1)).toContainText("Confirmado manualmente");
  await evidence(page, "17-triagem-historico-conflito");
});
