import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RadarPage } from "@/pages/radar-page";
import { buildPreset } from "@/test/factories";
import { json, mockApi, renderRoute } from "@/test/render";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Radar multitribunal", () => {
  it("preselects validated options, blocks inconclusive ones, and submits the selected group", async () => {
    const groupId = "66666666-6666-4666-8666-666666666666";
    let submitted: Record<string, unknown> | null = null;
    const preset = buildPreset({
      availability: { environment: "real", enabled: true, label: "validado", reasons: [] },
    });
    mockApi({
      "GET /api/v1/presets": () => json({ environment: "real", items: [preset] }),
      "GET /api/v1/jobs": () => json({ items: [], page: 1, page_size: 5, total: 0 }),
      "GET /api/v1/tribunal-availability": () => json({
        selection_enabled: true,
        preset_id: preset.id,
        matrix_sha256: "a".repeat(64),
        allowlist_version: "2026-10-11",
        catalog_version: "1.1.0",
        tpu_version: "06/10/2026",
        global_reasons: [],
        tribunals: [
          { tribunal: "TJGO", s1_s2_state: "VALIDATED", preset_state: "VALIDATED", enabled: true, reason: null },
          { tribunal: "TJMT", s1_s2_state: "VALIDATED", preset_state: "VALIDATED", enabled: true, reason: null },
          { tribunal: "TRF1", s1_s2_state: "VALIDATED", preset_state: "INCONCLUSIVE", enabled: false, reason: "S5: INCONCLUSIVE para TRF1/rural.credito_contratos." },
        ],
      }),
      "POST /api/v1/job-groups": async (request) => {
        submitted = await request.json() as Record<string, unknown>;
        return json({
          id: groupId,
          kind: "discovery",
          state: "queued",
          matrix_sha256: "a".repeat(64),
          allowlist_version: "2026-10-11",
          catalog_version: "1.1.0",
          tpu_version: "06/10/2026",
          request_snapshot: {},
          member_count: 1,
          completed_count: 0,
          created_at: "2026-10-12T12:00:00Z",
          updated_at: "2026-10-12T12:00:00Z",
          finished_at: null,
          members: [],
        });
      },
    });
    renderRoute(<RadarPage />, {
      path: "/radar",
      url: "/radar",
      environment: {
        environment: "real",
        source: "datajud",
        source_enabled: true,
        source_disabled_reason: null,
      },
    });

    const tjgo = await screen.findByRole("checkbox", { name: /TJGO/ });
    const tjmt = screen.getByRole("checkbox", { name: /TJMT/ });
    const trf1 = screen.getByRole("checkbox", { name: /TRF1/ });
    expect(tjgo).toBeChecked();
    expect(tjmt).toBeChecked();
    expect(trf1).toBeDisabled();

    await userEvent.click(tjgo);
    await userEvent.click(screen.getByRole("button", { name: "Iniciar coleta" }));

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(`/job-groups/${groupId}`));
    expect(submitted).toMatchObject({
      kind: "discovery",
      criteria: { preset_id: preset.id },
      tribunals: ["TJMT"],
    });
  });
});
