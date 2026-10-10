import { expect, test } from "@playwright/test";

import {
  evidence,
  expectJobStatus,
  expectNoHorizontalScroll,
  startCollection,
  startWorker,
  stopWorker,
  trackApiRequests,
} from "./support";

test.describe.configure({ mode: "serial" });

test.afterEach(() => {
  startWorker();
});

test("AC3: falha de rede mantém dados antigos identificados e retry não cria coleta", async ({
  page,
}) => {
  stopWorker();
  const requests = trackApiRequests(page);
  const jobId = await startCollection(page);
  await expectJobStatus(page, "Na fila");

  // Simulated network failure on the polled read.
  await page.route(`**/api/v1/jobs/${jobId}`, (route) => route.abort("internetdisconnected"));
  await expect(page.getByText(/Falha ao atualizar — exibindo dados possivelmente desatualizados/)).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Na fila");
  await evidence(page, "07-falha-com-cache");

  await page.unroute(`**/api/v1/jobs/${jobId}`);
  await page.getByRole("button", { name: "Tentar novamente" }).click();
  await expect(page.getByText(/Falha ao atualizar/)).toHaveCount(0);
  expect(requests.count("POST", /^\/api\/v1\/jobs$/)).toBe(1);

  await page.getByRole("button", { name: "Cancelar coleta" }).click();
  await expectJobStatus(page, "Cancelado");
});

test("AC3: falha sem cache é erro com retry, não lista vazia", async ({ page }) => {
  await page.route("**/api/v1/processes?*", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "database_unavailable",
          message: "O banco de dados está temporariamente indisponível.",
          request_id: "e2e",
          details: null,
        },
      }),
    }),
  );
  await page.goto("/processes");
  await expect(page.getByText("Não foi possível carregar os processos")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/Nenhum processo/)).toHaveCount(0);
  await evidence(page, "08-falha-sem-cache");

  await page.unroute("**/api/v1/processes?*");
  await page.getByRole("button", { name: "Tentar novamente" }).click();
  await expect(page.getByText("Não foi possível carregar os processos")).toHaveCount(0);
  // Either outcome is a real answer now; which one depends on earlier collections.
  await expect(page.locator('table, [data-state="empty"]').first()).toBeVisible();
});

test("AC3: vazio, falha de job, parcial e 404 são distinguíveis", async ({ page }) => {
  await page.goto("/processes?subject=assunto-inexistente-e2e");
  await expect(page.getByText("Nenhum processo corresponde aos filtros")).toBeVisible();
  await evidence(page, "09-vazio");

  // Simulated failed job (persisted state rewritten in the response only).
  const jobId = await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.route(`**/api/v1/jobs/${jobId}`, async (route) => {
    const response = await route.fetch();
    const job = await response.json();
    await route.fulfill({
      response,
      json: {
        ...job,
        status: "failed",
        reason: "retry_exhausted",
        attempts: [
          ...job.attempts,
          {
            id: "00000000-0000-4000-8000-000000000001",
            attempt_number: job.attempts.length + 1,
            started_at: job.created_at,
            finished_at: job.created_at,
            outcome: "failed",
            error_code: "source_timeout",
            error_summary: "Simulação: a fonte não respondeu.",
          },
        ],
      },
    });
  });
  await page.reload();
  await expectJobStatus(page, "Falhou");
  await expect(page.getByText("A coleta falhou: Tentativas esgotadas")).toBeVisible();
  await expect(page.getByText(/Simulação: a fonte não respondeu\./).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Retomar coleta" })).toBeVisible();
  await evidence(page, "10-job-falhou-simulado");
  await page.unroute(`**/api/v1/jobs/${jobId}`);

  await page.goto("/jobs/00000000-0000-4000-8000-00000000dead");
  await expect(page.getByRole("heading", { name: "Coleta não encontrada" })).toBeVisible();
  await page.goto("/processes/00000000-0000-4000-8000-00000000dead");
  await expect(page.getByRole("heading", { name: "Processo não encontrado" })).toBeVisible();
  await evidence(page, "11-404");
});

test("AC2: reload e link direto preservam filtros, página e contexto", async ({ page, context }) => {
  const jobId = await startCollection(page);
  await expectJobStatus(page, "Concluído");
  await page.reload();
  await expectJobStatus(page, "Concluído");

  await page.goto("/jobs?status=completed&kind=discovery");
  await expect(page.getByLabel("Estado")).toHaveValue("completed");
  await page.reload();
  await expect(page.getByLabel("Estado")).toHaveValue("completed");
  await expect(page.getByLabel("Tipo")).toHaveValue("discovery");
  await expect(page.getByRole("table")).toBeVisible();

  await page.goto("/processes");
  await page.getByLabel("Classe").fill("Classe TPU");
  await page.getByLabel("Preset").fill("rural.credito_contratos");
  await page.getByRole("button", { name: "Pesquisar" }).click();
  await expect(page).toHaveURL(/class=Classe\+TPU/);
  await expect(page).toHaveURL(/preset_id=rural.credito_contratos/);
  const url = page.url();
  await page.reload();
  await expect(page.getByLabel("Classe")).toHaveValue("Classe TPU");
  await expect(page.getByLabel("Preset")).toHaveValue("rural.credito_contratos");
  await expect(page.getByRole("table")).toBeVisible();

  const direct = await context.newPage();
  await direct.goto(url);
  await expect(direct.getByLabel("Classe")).toHaveValue("Classe TPU");
  await direct.goto(`/jobs/${jobId}`);
  await expectJobStatus(direct, "Concluído");
  await direct.goto("/processes?page=999");
  await expect(direct.getByText("Esta página não contém processos")).toBeVisible();
  await expect(direct.getByText(/Página 999/)).toBeVisible();
});

test("AC5: teclado, foco visível e largura de 390px", async ({ page }) => {
  await page.goto("/radar");
  await expect(page.getByRole("radio", { name: "Crédito e contratos rurais" })).toBeChecked();
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Pular para o conteúdo" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  const overviewLink = page
    .getByRole("navigation", { name: "Principal" })
    .getByRole("link", { name: "Visão geral" });
  await expect(overviewLink).toBeFocused();
  await page.keyboard.press("Tab");
  const radarLink = page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Radar" });
  await expect(radarLink).toBeFocused();
  const outline = await radarLink.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(outline).not.toBe("none");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/jobs$/);

  // Keyboard-only submission from the radar form.
  const requests = trackApiRequests(page);
  await page.goto("/radar");
  await expect(page.getByRole("radio", { name: "Crédito e contratos rurais" })).toBeChecked();
  await page.getByLabel("Limite de registros desta execução").focus();
  await page.keyboard.press("Control+A");
  await page.keyboard.type("5");
  await page.keyboard.press("Enter");
  await page.waitForURL(/\/jobs\/[0-9a-f-]{36}$/);
  expect(requests.count("POST", /^\/api\/v1\/jobs$/)).toBe(1);
  await expectJobStatus(page, "Concluído");
  const jobUrl = page.url();

  await page.setViewportSize({ width: 390, height: 844 });
  for (const [name, target] of [
    ["12-mobile-radar", "/radar"],
    ["13-mobile-jobs", "/jobs"],
    ["14-mobile-job", jobUrl],
    ["15-mobile-processos", "/processes"],
  ] as const) {
    await page.goto(target);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.waitForLoadState("networkidle");
    await expectNoHorizontalScroll(page);
    await evidence(page, name);
  }
  await page.getByRole("table").getByRole("link").first().click();
  await expect(page.getByTestId("movement").first()).toBeVisible();
  await expectNoHorizontalScroll(page);
  await evidence(page, "16-mobile-processo");
});
