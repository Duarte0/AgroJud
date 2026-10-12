import { expect, test } from "@playwright/test";

const groupId = "66666666-6666-4666-8666-666666666666";
const timestamp = "2026-10-12T12:00:00Z";

test("seleciona tribunais validados e mostra o resultado parcial por membro", async ({ page }) => {
  let submitted: Record<string, unknown> | null = null;
  await page.setViewportSize({ width: 390, height: 844 });

  const group = {
    id: groupId,
    kind: "discovery",
    state: "partial",
    matrix_sha256: "a".repeat(64),
    allowlist_version: "2026-10-11",
    catalog_version: "1.1.0",
    tpu_version: "06/10/2026",
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
        availability_state: "VALIDATED",
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
        availability_state: "VALIDATED",
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
          matrix_sha256: "a".repeat(64),
          allowlist_version: "2026-10-11",
          catalog_version: "1.1.0",
          tpu_version: "06/10/2026",
          global_reasons: [],
          tribunals: [
            { tribunal: "TJGO", s1_s2_state: "VALIDATED", preset_state: "VALIDATED", enabled: true, reason: null },
            { tribunal: "TJMT", s1_s2_state: "VALIDATED", preset_state: "VALIDATED", enabled: true, reason: null },
            { tribunal: "TJMS", s1_s2_state: "VALIDATED", preset_state: "VALIDATED", enabled: true, reason: null },
            { tribunal: "TJPR", s1_s2_state: "VALIDATED", preset_state: "VALIDATED", enabled: true, reason: null },
            { tribunal: "TRF1", s1_s2_state: "VALIDATED", preset_state: "NOT_APPLICABLE", enabled: false, reason: "S5: NOT_APPLICABLE para este preset." },
          ],
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
  await expect(page.getByRole("checkbox", { name: /TRF1/ })).toBeDisabled();
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
    tribunals: ["TJGO", "TJMT", "TJMS", "TJPR"],
  });
});
