import { expect, test } from "@playwright/test";

const groupId = "66666666-6666-4666-8666-666666666666";
const timestamp = "2026-10-12T12:00:00Z";
const tribunals = [
  "TJAC", "TJAL", "TJAM", "TJAP", "TJBA", "TJCE", "TJDFT", "TJES", "TJGO", "TJMA",
  "TJMG", "TJMS", "TJMT", "TJPA", "TJPB", "TJPE", "TJPI", "TJPR", "TJRJ", "TJRN",
  "TJRO", "TJRR", "TJRS", "TJSC", "TJSE", "TJSP", "TJTO", "TRF1", "TRF2", "TRF3",
  "TRF4", "TRF5", "TRF6",
];

test("seleciona tribunais da allowlist e mostra resultado parcial por membro", async ({ page }) => {
  let submitted: Record<string, unknown> | null = null;
  await page.setViewportSize({ width: 390, height: 844 });

  const group = {
    id: groupId,
    kind: "discovery",
    state: "partial",
    matrix_sha256: null,
    allowlist_version: "2026-10-11",
    catalog_version: null,
    tpu_version: null,
    request_snapshot: {},
    member_count: 2,
    completed_count: 1,
    created_at: timestamp,
    updated_at: timestamp,
    finished_at: timestamp,
    members: [
      {
        id: "77777777-7777-4777-8777-777777777777",
        tribunal: "TJGO",
        state: "COMPLETED",
        availability_state: "NOT_REQUIRED",
        reason: null,
        job_id: "88888888-8888-4888-8888-888888888888",
        collection_id: "99999999-9999-4999-8999-999999999999",
        coverage: {
          hits_confirmed: 3,
          pages_confirmed: 2,
          valid_hits: 3,
          rejected_hits: 0,
          new_count: 3,
          updated_count: 0,
          unchanged_count: 0,
        },
        next_page: 3,
        checkpoint_revision: 2,
        checkpoint_updated_at: timestamp,
        created_at: timestamp,
        updated_at: timestamp,
      },
      {
        id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        tribunal: "TJMT",
        state: "FAILED",
        availability_state: "NOT_REQUIRED",
        reason: "A fonte ficou indisponível; o checkpoint foi preservado.",
        job_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        collection_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        coverage: {
          hits_confirmed: 1,
          pages_confirmed: 1,
          valid_hits: 1,
          rejected_hits: 0,
          new_count: 1,
          updated_count: 0,
          unchanged_count: 0,
        },
        next_page: 2,
        checkpoint_revision: 1,
        checkpoint_updated_at: timestamp,
        created_at: timestamp,
        updated_at: timestamp,
      },
    ],
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/v1/environment") {
      await route.fulfill({
        json: {
          environment: "real",
          source: "datajud",
          source_enabled: true,
          source_disabled_reason: null,
        },
      });
      return;
    }
    if (path === "/api/v1/presets") {
      await route.fulfill({
        json: {
          environment: "real",
          items: [
            {
              id: "rural.credito_contratos",
              version: "1.0.0",
              family: "rural_credit",
              name: "Crédito rural e contratos",
              purpose: "Encontrar processos de crédito rural.",
              justification: "Preset sintético de teste.",
              filters: { class_codes: [], subject_codes: [] },
              include_descendants: false,
              rural_link_status: "direct",
              capture_explanation: "Classificação por critérios públicos do preset.",
              evidence: {},
              required_for_real: [],
              availability: { environment: "real", enabled: true, reasons: [], label: "validado" },
            },
          ],
        },
      });
      return;
    }
    if (path === "/api/v1/tribunal-availability") {
      await route.fulfill({
        json: {
          selection_enabled: true,
          preset_id: "rural.credito_contratos",
          matrix_sha256: null,
          allowlist_version: "2026-10-11",
          catalog_version: null,
          tpu_version: null,
          global_reasons: [],
          tribunals: tribunals.map((tribunal) => ({
            tribunal,
            s1_s2_state: "NOT_REQUIRED",
            preset_state: "NOT_REQUIRED",
            enabled: true,
            reason: null,
          })),
        },
      });
      return;
    }
    if (path === "/api/v1/jobs" && method === "GET") {
      await route.fulfill({ json: { items: [], page: 1, page_size: 5, total: 0 } });
      return;
    }
    if (path === "/api/v1/job-groups" && method === "POST") {
      submitted = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({ json: group });
      return;
    }
    if (path === `/api/v1/job-groups/${groupId}` && method === "GET") {
      await route.fulfill({ json: group });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "Resposta não configurada no teste." } });
  });

  await page.goto("/radar");

  await expect(page.getByRole("region", { name: "Ambiente" })).toContainText("Real — DataJud");
  await expect(page.getByRole("checkbox", { name: /TJGO/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /TJMT/ })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: /TRF1/ })).toBeChecked();
  const selector = page.getByRole("group", { name: "Tribunais para esta execução" });
  await expect(selector.getByRole("checkbox")).toHaveCount(33);
  await selector.getByRole("button", { name: "Limpar seleção" }).click();
  await selector.getByRole("checkbox", { name: /TJGO/ }).check();
  await selector.getByRole("checkbox", { name: /TJMT/ }).check();
  await page.getByRole("button", { name: "Iniciar coleta" }).click();

  await expect(page).toHaveURL(new RegExp(`/job-groups/${groupId}$`));
  await expect(page.getByText("Resultado concluído parcialmente", { exact: true })).toBeVisible();
  await expect(page.getByTestId("job-group-member")).toHaveCount(2);
  await expect(page.getByTestId("job-group-member").filter({ hasText: "TJGO" })).toContainText("3");
  await expect(page.getByTestId("job-group-member").filter({ hasText: "TJMT" })).toContainText(
    "A fonte ficou indisponível; o checkpoint foi preservado.",
  );
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true);
  expect(submitted).toMatchObject({
    kind: "discovery",
    criteria: { preset_id: "rural.credito_contratos" },
    tribunals: ["TJGO", "TJMT"],
  });
});
