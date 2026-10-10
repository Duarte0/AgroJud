import { expect, test } from "@playwright/test";

import { startWorker, stopWorker } from "./support";

test.describe.configure({ mode: "serial" });

test.afterEach(() => {
  startWorker();
});

test("salva os critérios do radar e ativa a atualização diária", async ({ page }) => {
  stopWorker();
  await page.goto("/radar");
  const name = `Busca E2E ${Date.now()}`;

  await page.locator("summary").filter({hasText: "Salvar busca"}).click();
  await page.getByLabel("Nome da busca salva").fill(name);
  await page.getByRole("button", { name: "Salvar busca" }).click();

  await page.getByRole("tab", { name: "Buscas salvas" }).click();
  const row = page.getByTestId("saved-search").filter({ hasText: name });
  await expect(row).toContainText("Agenda desativada");
  await row.getByRole("button", { name: "Ativar" }).click();
  await expect(row).toContainText("Agenda ativa");
  await expect(row).toContainText("próxima atualização");
});
