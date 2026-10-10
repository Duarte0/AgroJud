import { expect, test } from "@playwright/test";
import { evidence, startCollection, expectJobStatus } from "./support";

test("redesign preserva contexto, rascunho e protege a saída", async ({ page }) => {
  await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await page.waitForURL(/\/processes\?collection_id=/);
  const listUrl = page.url();
  await page.getByRole("link", { name: "0000001-00.2026.8.09.0001" }).click();
  await page.waitForURL(/\/processes\/[0-9a-f-]{36}/);
  await page.getByLabel("Nota de revisão").fill("Rascunho entre abas");
  await page.getByRole("tab", { name: "Capas por origem" }).click();
  await page.getByRole("tab", { name: "Sinais" }).click();
  await expect(page.getByLabel("Nota de revisão")).toHaveValue("Rascunho entre abas");
  await page.getByRole("link", { name: "Voltar à lista" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("Sair sem salvar a triagem?");
  await page.getByRole("button", { name: "Voltar", exact: true }).click();
  await expect(page.getByLabel("Nota de revisão")).toHaveValue("Rascunho entre abas");
  await page.getByRole("link", { name: "Voltar à lista" }).click();
  await page.getByRole("button", { name: "Sair sem salvar", exact: true }).click();
  await expect(page).toHaveURL(listUrl);
  await expect(page.getByRole("list", { name: "Filtros aplicados" })).toContainText("Coleta");
  await page.getByRole("button", { name: "Remover filtro Coleta" }).click();
  await expect(page).not.toHaveURL(/collection_id/);
});

test("redesign mantém navegação móvel, foco e dados extensos sem overflow", async ({ page }) => {
  await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await page.waitForURL(/\/processes\?collection_id=/);
  await page.getByRole("link", { name: "0000001-00.2026.8.09.0001" }).click();
  await page.waitForURL(/\/processes\/[0-9a-f-]{36}/);
  const detailUrl = page.url();
  // Stress layout with a source description, without mutating persisted data.
  await page.route("**/api/v1/processes/*/representations?*", async route => {
    const response = await route.fetch();
    const body = await response.json();
    for (const item of body.items) item.court_unit_name = "Vara Cível com atribuições extensas e descrição de origem ".repeat(8);
    await route.fulfill({ response, json: body });
  });
  // 640 CSS pixels represents a 1280px desktop viewed at 200% browser zoom.
  for (const width of [390, 640, 768, 1024, 1440]) {
    await page.setViewportSize({width, height: 960});
    for (const route of ["/", "/processes", "/radar", "/jobs", "/watchlist", "/news", detailUrl]) {
      await page.goto(route);
      await expect(page.getByRole("heading", {level: 1})).toBeVisible();
      if (route === detailUrl) {
        await expect(page.getByRole("heading", {level: 1})).toContainText("0000001-00.2026.8.09.0001");
      }
      await expect(page.locator("main [role=status]").filter({hasText: /^Carregando/})).toHaveCount(0);
      if (route === "/processes" && width <= 640) {
        await expect(page.getByRole("table")).toBeVisible();
        await expect(page.getByRole("columnheader", { name: "Número CNJ" })).toHaveCount(1);
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    await evidence(page, `redesign-detalhe-${width}`);
  }
  await page.setViewportSize({width: 390, height: 844});
  const menu = page.getByRole("button", {name: "Abrir navegação"});
  await menu.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await menu.click();
  await page.getByRole("dialog").getByRole("link", {name: "Processos", exact: true}).click();
  await expect(page).toHaveURL(/\/processes$/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await evidence(page, "redesign-processos-mobile");
});

test("radar mantém critérios ao alternar buscas e apresenta erro junto ao campo", async ({ page }) => {
  await page.goto("/radar");
  await page.getByLabel("Limite de registros desta execução").fill("37");
  await page.getByRole("tab", {name: "Buscas salvas"}).click();
  await page.getByRole("tab", {name: "Nova coleta"}).click();
  await expect(page.getByLabel("Limite de registros desta execução")).toHaveValue("37");
  await page.locator("summary").filter({hasText: "Salvar busca"}).click();
  await page.getByRole("button", {name: "Salvar busca", exact: true}).click();
  await expect(page.getByLabel("Nome da busca salva")).toBeFocused();
  await expect(page.getByLabel("Nome da busca salva")).toHaveAttribute("aria-invalid", "true");
  await evidence(page, "redesign-radar-validacao");
});
