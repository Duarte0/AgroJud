import { expect, test } from "@playwright/test";

import {
  expectJobStatus,
  seedPendingNews,
  setProcessNumber,
  startCollection,
  uniqueProcessNumber,
} from "./support";

test.describe.configure({ mode: "serial" });

test("exibe baseline pendente, separa as datas e permite revisar e reabrir novidade", async ({
  page,
}) => {
  await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await page.getByRole("link", { name: "0000001-00.2026.8.09.0001" }).click();

  const processId = page.url().split("/").pop()!;
  setProcessNumber(processId, uniqueProcessNumber());
  await page.reload();
  await page.getByRole("button", { name: "Acompanhar processo" }).click();
  await expect(page.getByText("Acompanhamento ativo")).toBeVisible();
  expect(seedPendingNews(processId)).toMatch(/^[0-9a-f-]{36}$/i);

  await page.reload();
  await expect(page.getByText(/Baseline pendente/)).toBeVisible();
  await page.getByRole("link", { name: "Novidades" }).click();
  const item = page.getByTestId("news-item");
  await expect(item).toContainText("Conteúdo recém-observado");
  await expect(item).toContainText("01/04/2020, 00:04");
  await expect(item).toContainText("09/10/2026, 09:00");
  await expect(item).toContainText("isso não afirma que seja um novo ato jurídico");

  await item.getByRole("button", { name: "Marcar como revisada" }).click();
  await expect(item.getByText("Revisada")).toBeVisible();
  await item.getByRole("button", { name: "Reabrir revisão" }).click();
  await expect(item.getByText("Pendente")).toBeVisible();

  await page.goto(`/processes/${processId}`);
  await page.getByRole("button", { name: "Remover acompanhamento" }).click();
  await expect(page.getByText("Não acompanhado")).toBeVisible();
});
