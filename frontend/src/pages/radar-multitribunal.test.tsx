import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RadarPage } from "@/pages/radar-page";
import { buildSearchType } from "@/test/factories";
import { json, mockApi, renderRoute } from "@/test/render";

const TRIBUNALS = [
  "TJAC", "TJAL", "TJAM", "TJAP", "TJBA", "TJCE", "TJDFT", "TJES", "TJGO", "TJMA",
  "TJMG", "TJMS", "TJMT", "TJPA", "TJPB", "TJPE", "TJPI", "TJPR", "TJRJ", "TJRN",
  "TJRO", "TJRR", "TJRS", "TJSC", "TJSE", "TJSP", "TJTO", "TRF1", "TRF2", "TRF3",
  "TRF4", "TRF5", "TRF6",
];
const groupId = "66666666-6666-4666-8666-666666666666";

function setup(onSubmit: (body: Record<string, unknown>) => void) {
  const theme = buildSearchType({
    availability: { environment: "real", enabled: true, label: "disponível", reasons: [] },
  });
  mockApi({
    "GET /api/v1/search-types": () => json({ environment: "real", items: [theme] }),
    "GET /api/v1/custom-searches": () => json([]),
    "GET /api/v1/tpu-codes": () => json([]),
    "GET /api/v1/jobs": () => json({ items: [], page: 1, page_size: 5, total: 0 }),
    "GET /api/v1/tribunal-availability": () => json({
      selection_enabled: true,
      preset_id: theme.id,
      matrix_sha256: null,
      allowlist_version: "2026-10-11",
      catalog_version: null,
      tpu_version: null,
      global_reasons: [],
      tribunals: TRIBUNALS.map((tribunal) => ({
        tribunal,
        s1_s2_state: "NOT_REQUIRED",
        preset_state: "NOT_REQUIRED",
        enabled: true,
        reason: null,
      })),
    }),
    "POST /api/v1/job-groups": async (request) => {
      onSubmit(await request.json() as Record<string, unknown>);
      return json({
        id: groupId,
        kind: "discovery",
        state: "queued",
        matrix_sha256: null,
        allowlist_version: "2026-10-11",
        catalog_version: null,
        tpu_version: null,
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
  return theme;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Radar multitribunal", () => {
  it("starts with all 33 tribunals selected and submits a national group", async () => {
    let submitted: Record<string, unknown> | null = null;
    const theme = setup((body) => { submitted = body; });
    const selector = await screen.findByRole("group", { name: "Tribunais para esta execução" });
    const checkboxes = within(selector).getAllByRole("checkbox");

    expect(checkboxes).toHaveLength(33);
    expect(checkboxes.every((checkbox) => (checkbox as HTMLInputElement).checked)).toBe(true);
    expect(within(selector).getByText("Coleta nacional: todos os 33 tribunais.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Iniciar coleta" }));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(`/job-groups/${groupId}`));
    expect(submitted).toMatchObject({
      kind: "discovery",
      criteria: {
        search_definition: {
          kind: "official_theme",
          id: theme.id,
          version: theme.version,
        },
      },
      tribunals: TRIBUNALS,
    });
  });

  it("allows narrowing the selection to one tribunal", async () => {
    let submitted: Record<string, unknown> | null = null;
    setup((body) => { submitted = body; });
    const selector = await screen.findByRole("group", { name: "Tribunais para esta execução" });

    await userEvent.click(within(selector).getByRole("button", { name: "Limpar seleção" }));
    await userEvent.click(within(selector).getByRole("checkbox", { name: /TJMT/ }));
    await userEvent.click(screen.getByRole("button", { name: "Iniciar coleta" }));

    await waitFor(() => expect(submitted).not.toBeNull());
    expect(submitted).toMatchObject({ tribunals: ["TJMT"] });
  });
});
