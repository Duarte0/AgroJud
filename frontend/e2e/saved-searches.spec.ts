import { expect, test } from "@playwright/test";

import { evidence, startWorker, stopWorker } from "./support";

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
  await form.getByRole("checkbox", { name: /Usar a janela padrão/ }).uncheck();
  await form.getByLabel("Ajuizados a partir de").fill("2025-01-01");
  await form.getByLabel("Ajuizados até (inclusive)").fill("2025-04-01");
  await form.getByLabel("Limite cumulativo de registros").fill("321");
  const availableThemes = form.locator('input[type="radio"]:not(:disabled)');
  await expect(availableThemes).toHaveCount(3);
  await availableThemes.nth(1).check();
  await form.getByLabel("Ajuizados a partir de").fill("2025-05-01");
  await form.getByRole("button", { name: "Salvar alterações" }).click();
  await expect(form.getByRole("alert")).toContainText("data inicial deve ser anterior ou igual à final");
  await expect(row.locator("h3")).toHaveText(name);
  await form.getByLabel("Ajuizados a partir de").fill("2025-01-01");
  await form.getByRole("button", { name: "Salvar alterações" }).click();

  const revisedRow = page.getByTestId("saved-search").filter({ hasText: `${name} revisada` });
  await expect(revisedRow.locator("h3")).toHaveText(`${name} revisada`);
  await expect(revisedRow).toContainText("Revisão 2");
  await expect(revisedRow).toContainText("Período fixo: 2025-01-01 a 2025-04-01");
  await expect(revisedRow).toContainText("Recuperação judicial");
  await expect(revisedRow).not.toContainText("recuperacao_judicial.por_");
  await expect(revisedRow).toContainText("Agenda desativada");
  await expect(revisedRow).toContainText("Nenhum disparo realizado ainda.");
  await expect(revisedRow.getByRole("button", { name: "Executar agora" })).toBeVisible();

  await revisedRow.getByRole("button", { name: "Editar" }).click();
  form = revisedRow.getByRole("form", { name: "Editar busca salva" });
  await expect(form.getByLabel("Ajuizados a partir de")).toHaveValue("2025-01-01");
  await expect(form.getByLabel("Ajuizados até (inclusive)")).toHaveValue("2025-04-01");
  await expect(form.getByLabel("Limite cumulativo de registros")).toHaveValue("321");
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

test("cria uma busca estruturada a partir do catálogo TPU", async ({ page }) => {
  stopWorker();
  await page.goto("/radar");
  const name = `Busca estruturada ${Date.now()}`;

  await page.getByRole("button", { name: "Criar busca personalizada" }).click();
  const editor = page.getByTestId("custom-search-editor");
  await editor.getByLabel("Nome").fill(name);
  await editor.getByLabel("Pesquisar classe no catálogo").fill("129");
  await editor.getByRole("checkbox", { name: "129 — Recuperação Judicial" }).check();
  await editor.getByLabel("Pesquisar assunto no catálogo").fill("13277");
  await editor.getByRole("checkbox", { name: "13277 — Recuperação Judicial" }).check();
  await editor.getByLabel("Combinar classes e assuntos").selectOption("OR");

  const creation = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().endsWith("/api/v1/custom-searches"),
  );
  await editor.getByRole("button", { name: "Salvar busca" }).click();
  const response = await creation;
  expect(response.status()).toBe(201);
  const definition = await response.json() as {
    id: string;
    version: number;
    criteria: { classes: Array<{ code: number }>; subjects: Array<{ code: number }>; dimension_combination: string };
  };
  expect(definition.version).toBe(1);
  expect(definition.criteria).toMatchObject({
    classes: [{ code: 129 }],
    subjects: [{ code: 13277 }],
    dimension_combination: "OR",
  });
  await expect(page.getByRole("radio", { name })).toBeVisible();
  await expect(page.getByRole("button", { name: "Iniciar coleta" })).toBeVisible();
  await expect(page).not.toHaveURL(/job-groups/);
  await evidence(page, "spec027-radar-busca-personalizada");
});

test("duplica um tema, cria nova versão ao editar e arquiva sem apagar o tema", async ({ page }) => {
  stopWorker();
  await page.goto("/radar");
  const name = `Cópia RJ ${Date.now()}`;
  const recoveryCard = page.locator('[data-search-definition="official_theme:tema.recuperacao_judicial:1.0.0"]');
  await recoveryCard.getByRole("button", { name: "Duplicar e personalizar" }).click();
  const editor = page.getByTestId("custom-search-editor");
  await editor.getByLabel("Nome").fill(name);

  const creation = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().endsWith("/api/v1/custom-searches"),
  );
  await editor.getByRole("button", { name: "Salvar busca" }).click();
  const createdResponse = await creation;
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json() as { id: string; version: number; criteria: { classes: Array<{ code: number }>; subjects: Array<{ code: number }> } };
  expect(created.version).toBe(1);
  expect(created.criteria.classes.map((item) => item.code)).toEqual([129]);
  expect(created.criteria.subjects.map((item) => item.code)).toEqual([13277]);
  const customCard = page.locator(`[data-search-definition="custom_search:${created.id}:1"]`);
  await expect(customCard).toBeVisible();

  await customCard.getByRole("button", { name: "Editar" }).click();
  const editForm = page.getByTestId("custom-search-editor");
  await editForm.getByLabel("Nome").fill(`${name} revisada`);
  const revision = page.waitForResponse((response) =>
    response.request().method() === "PATCH" && response.url().endsWith(`/api/v1/custom-searches/${created.id}`),
  );
  await editForm.getByRole("button", { name: "Salvar nova versão" }).click();
  const revisionResponse = await revision;
  expect(revisionResponse.status()).toBe(200);
  expect((await revisionResponse.json() as { version: number }).version).toBe(2);
  const revisedCard = page.locator(`[data-search-definition="custom_search:${created.id}:2"]`);
  await expect(revisedCard).toBeVisible();

  const archive = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().endsWith(`/api/v1/custom-searches/${created.id}/archive`),
  );
  await revisedCard.getByRole("button", { name: "Arquivar" }).click();
  expect((await archive).status()).toBe(200);
  await expect(page.getByRole("radio", { name: `${name} revisada` })).toHaveCount(0);
  await expect(page.getByRole("radio", { name: "Recuperação judicial" })).toBeVisible();
});
