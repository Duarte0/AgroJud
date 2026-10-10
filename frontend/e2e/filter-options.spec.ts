import { expect, test } from "@playwright/test";

import { evidence, expectJobStatus, startCollection } from "./support";

test("filtros de processo sugerem valores canônicos e ocultam o UUID da coleta", async ({ page }) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", error => runtimeErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") runtimeErrors.push(message.text());
  });

  await page.goto("/radar");
  await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await expect(page).toHaveURL(/\/processes\?collection_id=/);

  const subject = page.getByLabel("Assunto");
  await subject.fill("assunto");
  await page.getByRole("option", { name: /Assunto TPU 10501/ }).waitFor();
  await evidence(page, "spec022-autocomplete-assunto");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(subject).toHaveValue("Assunto TPU 10501");
  await page.getByRole("button", { name: "Pesquisar" }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("subject")).toBe("Assunto TPU 10501");

  await page.getByRole("button", { name: /Mais filtros/ }).click();
  const collection = page.getByRole("combobox", { name: "Coleta" });
  await expect(collection).toHaveValue(/\d{2}\/\d{2}\/\d{4}.*rural\.credito_contratos · Concluída/);
  await expect(collection).not.toHaveValue(/[0-9a-f]{8}-[0-9a-f-]{27}/i);

  const classFilter = page.getByLabel("Classe");
  await classFilter.fill("classe");
  await page.getByRole("option", { name: /Classe TPU 1116/ }).waitFor();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(classFilter).toHaveValue("Classe TPU 1116");
  await page.getByRole("button", { name: "Pesquisar" }).click();
  await expect.poll(() => new URL(page.url()).searchParams.get("class")).toBe("Classe TPU 1116");

  await evidence(page, "spec022-processos-filtros-aplicados");

  const collectionId = new URL(page.url()).searchParams.get("collection_id");
  expect(collectionId).toMatch(/[0-9a-f-]{36}/i);
  await page.goto(`/?collection_id=${collectionId}`);
  await expect(page.getByRole("heading", { name: "Visão geral" })).toBeVisible();
  await expect(page.getByText(/\d{2}\/\d{2}\/\d{4}.*rural\.credito_contratos · Concluída/)).toBeVisible();
  await expect(page.locator("main")).not.toContainText(collectionId!);
  expect(runtimeErrors).toEqual([]);
  await evidence(page, "spec022-sugestoes-filtros-processos");
});
