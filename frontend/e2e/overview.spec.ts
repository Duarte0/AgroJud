import { expect, test } from "@playwright/test";

import { evidence, expectJobStatus, startCollection } from "./support";

test("visão geral local abre a lista pelo indicador", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Visão geral" })).toBeVisible();

  await startCollection(page);
  await expectJobStatus(page, "Concluído");

  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Visão geral" })).toBeVisible();
  await expect(page.getByText("Última observação local:")).toBeVisible();
  const processMetric = page.getByRole("link", { name: /^Processos distintos:/ });
  const processCountText = await processMetric.innerText();
  const processCount = Number(processCountText.match(/(\d+)\s+process(?:o|os)\b/)?.[1]);
  expect(processCount).toBeGreaterThan(0);
  await evidence(page, "spec018-visao-geral");

  await processMetric.click();
  await expect(page).toHaveURL(/\/processes$/);
  await expect(page.getByRole("heading", { level: 1, name: "Processos" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Paginação dos processos" })).toContainText(
    `${processCount} ${processCount === 1 ? "registro" : "registros"}`,
  );

  await page.goto("/");
  const themeLink = page.getByRole("table").first().getByRole("link").first();
  await expect(themeLink).toHaveAttribute("href", /\/processes\?subject_code=/);
  await themeLink.click();
  await expect(page).toHaveURL(/\/processes\?subject_code=/);
  const exactThemeName = await page.getByLabel("Nome exato do tema").inputValue();
  expect(exactThemeName).not.toBe("");

  await page.getByRole("link", { name: "Ver indicadores deste recorte" }).click();
  await expect(page).toHaveURL(/\/\?subject_code=/);
  await expect(page.getByText("Código exato do tema")).toBeVisible();
  await expect(page.getByRole("definition").filter({ hasText: exactThemeName })).toBeVisible();
  await evidence(page, "spec018-visao-geral");
});
