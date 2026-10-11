import { expect, test } from "@playwright/test";

import { startWorker, stopWorker } from "./support";

test.describe.configure({ mode: "serial" });

test.afterEach(() => {
  startWorker();
});

test("edita os critérios da busca salva e preserva o controle de execução", async ({ page }) => {
  stopWorker();
  await page.goto("/radar");
  const name = `Busca E2E ${Date.now()}`;

  await page.locator("summary").filter({hasText: "Salvar busca"}).click();
  await page.getByLabel("Nome da busca salva").fill(name);
  await page.getByRole("button", { name: "Salvar busca" }).click();

  await page.getByRole("tab", { name: "Buscas salvas" }).click();
  const row = page.getByTestId("saved-search").filter({ hasText: name });
  await expect(row).toContainText("Agenda desativada");

  await row.getByRole("button", { name: "Editar" }).click();
  let form = row.getByRole("form", { name: "Editar busca salva" });
  await form.getByLabel("Nome da busca").fill("Rascunho cancelado");
  await form.getByRole("button", { name: "Cancelar" }).click();
  await expect(row.locator("h3")).toHaveText(name);

  await row.getByRole("button", { name: "Editar" }).click();
  form = row.getByRole("form", { name: "Editar busca salva" });
  await form.getByLabel("Nome da busca").fill(`${name} revisada`);
  await form.getByRole("checkbox").uncheck();
  await form.getByLabel("Ajuizados a partir de").fill("2025-01-01");
  await form.getByLabel("Ajuizados até (inclusive)").fill("2025-04-01");
  await form.getByLabel("Limite de registros").fill("321");
  const availablePresets = form.locator('input[type="radio"]:not(:disabled)');
  await expect(availablePresets).toHaveCount(4);
  const nextPresetId = await availablePresets.nth(1).getAttribute("value");
  await availablePresets.nth(1).check();
  await form.getByLabel("Ajuizados a partir de").fill("2025-05-01");
  await form.getByRole("button", { name: "Salvar alterações" }).click();
  await expect(form.getByRole("alert")).toContainText("data inicial deve ser anterior ou igual à final");
  await expect(row.locator("h3")).toHaveText(name);
  await form.getByLabel("Ajuizados a partir de").fill("2025-01-01");
  await form.getByRole("button", { name: "Salvar alterações" }).click();

  const revisedRow = page.getByTestId("saved-search").filter({ hasText: `${name} revisada` });
  await expect(revisedRow.locator("h3")).toHaveText(`${name} revisada`);
  await expect(revisedRow).toContainText("busca v2");
  await expect(revisedRow).toContainText("Período fixo: 2025-01-01 a 2025-04-01");
  await expect(revisedRow).toContainText(`${nextPresetId} · preset`);
  await expect(revisedRow).toContainText("Agenda desativada");
  await expect(revisedRow).toContainText("Nenhum disparo realizado ainda.");
  await expect(revisedRow.getByRole("button", { name: "Executar agora" })).toBeVisible();

  await revisedRow.getByRole("button", { name: "Editar" }).click();
  form = revisedRow.getByRole("form", { name: "Editar busca salva" });
  await expect(form.getByLabel("Ajuizados a partir de")).toHaveValue("2025-01-01");
  await expect(form.getByLabel("Ajuizados até (inclusive)")).toHaveValue("2025-04-01");
  await expect(form.getByLabel("Limite de registros")).toHaveValue("321");
  await form.getByRole("button", { name: "Cancelar" }).click();

  await revisedRow.getByRole("button", { name: "Ativar" }).click();
  await expect(revisedRow).toContainText("Agenda ativa");
  await expect(revisedRow).toContainText("próxima atualização");
  await expect(page).toHaveURL(/\/radar.*tab=saved/);
});

test("mantém a edição aberta e os valores quando o PATCH falha", async ({ page }) => {
  stopWorker();
  await page.goto("/radar");
  const name = `Busca E2E erro ${Date.now()}`;

  await page.locator("summary").filter({ hasText: "Salvar busca" }).click();
  await page.getByLabel("Nome da busca salva").fill(name);
  await page.getByRole("button", { name: "Salvar busca" }).click();
  await page.getByRole("tab", { name: "Buscas salvas" }).click();
  const row = page.getByTestId("saved-search").filter({ hasText: name });
  await row.getByRole("button", { name: "Editar" }).click();
  const form = row.getByRole("form", { name: "Editar busca salva" });
  await form.getByLabel("Nome da busca").fill(`${name} revisada`);

  await page.route("**/api/v1/saved-searches/**", async (route) => {
    if (route.request().method() === "PATCH") {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Falha simulada ao atualizar a busca." }),
      });
      return;
    }
    await route.continue();
  });
  await form.getByRole("button", { name: "Salvar alterações" }).click();
  await expect(form.getByRole("alert")).toContainText("A busca não foi atualizada");
  await expect(form.getByLabel("Nome da busca")).toHaveValue(`${name} revisada`);
  await expect(row.locator("h3")).toHaveText(name);
  await expect(row.getByRole("button", { name: "Executar agora" })).toHaveCount(0);
});
