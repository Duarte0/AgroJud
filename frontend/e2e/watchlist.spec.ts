import { expect, test } from "@playwright/test";

import { expectJobStatus, startCollection, startWorker } from "./support";

test.describe.configure({ mode: "serial" });

test.afterEach(() => {
  startWorker();
});

test("acompanha, atualiza por número e remove sem cancelar o resultado", async ({ page }) => {
  await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await page.getByRole("link", { name: "0000001-00.2026.8.09.0001" }).click();

  const processId = page.url().split("/").pop()!;
  await page.getByRole("button", { name: "Acompanhar processo" }).click();
  await expect(page.getByText("Acompanhamento ativo")).toBeVisible();
  await expect(page.getByTestId("watch-history-entry")).toHaveCount(1);

  await page.getByRole("button", { name: "Atualizar processo" }).click();
  await page.waitForURL(/\/jobs\/[0-9a-f-]{36}$/);
  await expectJobStatus(page, "Concluído");

  await page.goto("/watchlist");
  const entry = page.getByTestId("watchlist-entry");
  await expect(entry).toContainText("0000001-00.2026.8.09.0001");
  await expect(entry).toContainText("Encontrado na consulta");
  await entry.getByRole("button", { name: "Remover" }).click();
  await expect(page.getByText("Nenhum processo acompanhado")).toBeVisible();

  const audit = await page.request.get(
    new URL(`/api/v1/processes/${processId}/watch`, page.url()).toString(),
  );
  expect(audit.ok()).toBeTruthy();
  const state = await audit.json();
  expect(state.active).toBe(false);
  expect(state.history.map((event: { action: string }) => event.action)).toEqual([
    "included",
    "removed",
  ]);
  expect(state.last_refresh.state).toBe("found");
});
