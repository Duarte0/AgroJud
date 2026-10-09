import { expect, test } from "@playwright/test";

import { expectJobStatus, setProcessMovementCode, startCollection } from "./support";

test.describe.configure({ mode: "serial" });

test("sinal reprocessado abre sua ocorrência exata na timeline", async ({ page }) => {
  await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await page.getByRole("link", { name: "0000001-00.2026.8.09.0001" }).click();

  const processId = page.url().split("/").pop()!;
  const occurrenceId = setProcessMovementCode(processId, 11382);
  await page.getByRole("button", { name: "Reprocessar sinais deste processo" }).click();

  await expect(page.getByText("Último reprocessamento: Concluído")).toBeVisible({
    timeout: 30_000,
  });
  const signal = page.getByTestId("process-signal");
  await expect(signal).toHaveCount(1);
  await expect(signal).toContainText("Bloqueio, penhora ou arresto observado");
  await expect(signal).toContainText("sinal.penhora");

  await signal.getByRole("link", { name: "Ver evidência na linha do tempo" }).click();
  await expect(page).toHaveURL(new RegExp(`evidence=${occurrenceId}#timeline$`));
  const movement = page.locator(`#movement-${occurrenceId}`);
  await expect(movement).toContainText("Bloqueio, Penhora ou Arresto");
  await expect(movement).toContainText("código 11382");
});
