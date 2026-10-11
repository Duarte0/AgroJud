import { expect, test } from "@playwright/test";

import {
  addLongMovementComplement,
  expectJobStatus,
  setProcessMovementCode,
  startCollection,
} from "./support";

test.describe.configure({ mode: "serial" });

test("sinal reprocessado abre sua ocorrência exata na timeline", async ({ page }, testInfo) => {
  await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.getByRole("link", { name: "Ver processos desta coleta" }).click();
  await page.getByRole("link", { name: "0000001-00.2026.8.09.0001" }).click();
  await page.waitForURL(/\/processes\/[0-9a-f-]{36}$/i);

  const processId = page.url().split("/").pop()!;
  const occurrenceId = setProcessMovementCode(processId, 11382);
  addLongMovementComplement(processId, occurrenceId);
  await page.getByRole("tab", { name: "Sinais" }).click();
  await page.getByRole("button", { name: "Reprocessar sinais deste processo" }).click();

  await expect(page.getByText("Último reprocessamento: Concluído")).toBeVisible({
    timeout: 30_000,
  });
  const signal = page.getByTestId("process-signal");
  await expect(signal).toHaveCount(1);
  await expect(signal).toContainText("Bloqueio, penhora ou arresto observado");
  await expect(signal).toContainText("sinal.penhora");
  await expect(page.getByRole("region", { name: "Lista de sinais vigentes e históricos" })).toBeVisible();

  await signal.getByRole("link", { name: "Ver evidência na linha do tempo" }).click();
  await expect(page).toHaveURL(new RegExp(`evidence=${occurrenceId}#timeline$`));
  const movement = page.locator(`#movement-${occurrenceId}`);
  await expect(movement).toContainText("Bloqueio, Penhora ou Arresto");
  await expect(movement).toContainText("código 11382");
  await expect(movement).toBeFocused();
  await expect(movement).toHaveAttribute("data-evidence", "true");

  const list = page.getByRole("region", { name: "Lista de movimentações" });
  for (const width of [390, 640, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const dimensions = await list.evaluate((element) => {
      const style = window.getComputedStyle(element);
      return {
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        maxHeight: Number.parseFloat(style.maxHeight),
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
      };
    });
    expect(dimensions.scrollHeight).toBeGreaterThan(dimensions.clientHeight);
    expect(dimensions.clientHeight).toBeLessThanOrEqual(dimensions.maxHeight + 1);
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  }
  await list.evaluate((element) => { element.scrollTop = 0; });
  await list.hover();
  await page.mouse.wheel(0, 600);
  await expect.poll(() => list.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await page.screenshot({ path: testInfo.outputPath("movement-scroll.png") });
});
