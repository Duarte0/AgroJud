import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { OverviewPage } from "@/pages/overview-page";
import { apiError, json, mockApi, renderRoute } from "@/test/render";

afterEach(() => {
  vi.unstubAllGlobals();
});

function overview(overrides: Record<string, unknown> = {}) {
  return {
    generated_at: "2026-10-09T15:00:00Z",
    data_source: "synthetic",
    filters: {
      process_number: null,
      subject: null,
      subject_code: null,
      subject_name_exact: null,
      class: null,
      court_unit: null,
      collection_id: null,
      preset_id: null,
      decision: null,
      rural_link: null,
      followed: null,
      pending_news: null,
      signal_category: null,
    },
    processes: { value: 2, unit: "processes" },
    representations: { value: 3, unit: "representations" },
    triage: {
      pending: { value: 1, unit: "processes" },
      relevant: { value: 0, unit: "processes" },
      discarded: { value: 1, unit: "processes" },
    },
    followed_processes: { value: 1, unit: "processes" },
    pending_news: { value: 1, unit: "occurrences" },
    current_signals: [
      { category: "penhora", processes: { value: 1, unit: "processes" } },
    ],
    themes: [
      {
        subject_code: "4968",
        subject_name: "Crédito rural",
        processes: { value: 2, unit: "processes" },
      },
    ],
    latest_collections: [],
    latest_observation_at: "2026-10-09T14:00:00Z",
    ...overrides,
  };
}

describe("OverviewPage", () => {
  it("shows real zero counts for a successful empty base", async () => {
    mockApi({
      "GET /api/v1/overview": () =>
        json(
          overview({
            processes: { value: 0, unit: "processes" },
            representations: { value: 0, unit: "representations" },
            triage: {
              pending: { value: 0, unit: "processes" },
              relevant: { value: 0, unit: "processes" },
              discarded: { value: 0, unit: "processes" },
            },
            followed_processes: { value: 0, unit: "processes" },
            pending_news: { value: 0, unit: "occurrences" },
            current_signals: [],
            themes: [],
          }),
        ),
    });
    renderRoute(<OverviewPage />, { path: "/", url: "/" });

    expect(await screen.findByText("Nenhum processo neste recorte")).toBeInTheDocument();
    expect(screen.getAllByText("0 processos").length).toBeGreaterThan(0);
    expect(screen.getByText("0 representações")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "radar" })).toHaveAttribute("href", "/radar");
  });

  it("keeps database failure distinct from a zero-valued overview", async () => {
    mockApi({
      "GET /api/v1/overview": () =>
        apiError(503, "database_unavailable", "O banco de dados está temporariamente indisponível."),
    });
    renderRoute(<OverviewPage />, { path: "/", url: "/" });

    expect(await screen.findByText("Não foi possível carregar os indicadores")).toBeInTheDocument();
    expect(screen.queryByText("0 processos")).not.toBeInTheDocument();
    expect(screen.queryByText("Nenhum processo neste recorte")).not.toBeInTheDocument();
  });

  it("uses the metric unit with singular and plural labels", async () => {
    mockApi({ "GET /api/v1/overview": () => json(overview()) });
    renderRoute(<OverviewPage />, { path: "/", url: "/" });

    expect(
      await screen.findByRole("link", { name: /Processos distintos: 2 processos/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Acompanhados ativos: 1 processo/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Novidades pendentes: 1 novidade/ }),
    ).toBeInTheDocument();
  });

  it("sends resolved filters and opens matching process/news lists", async () => {
    const api = mockApi({
      "GET /api/v1/overview": () =>
        json(
          overview({
            filters: {
              ...overview().filters,
              subject_code: "4968",
              subject_name_exact: "Crédito rural",
            },
          }),
        ),
    });
    renderRoute(<OverviewPage />, {
      path: "/",
      url: "/?subject_code=4968&subject_name_exact=Cr%C3%A9dito+rural",
    });

    expect(await screen.findByRole("heading", { name: "Visão geral" })).toBeInTheDocument();
    const params = new URL(api.calls[0]!.url).searchParams;
    expect(params.get("subject_code")).toBe("4968");
    expect(params.get("subject_name_exact")).toBe("Crédito rural");

    await userEvent.click(await screen.findByRole("link", { name: /Novidades pendentes/ }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/news?subject_code=4968&subject_name_exact=Cr%C3%A9dito+rural&status=pending",
    );
  });

  it("drills down a theme with exact subject code and name filters", async () => {
    mockApi({
      "GET /api/v1/overview": () =>
        json(
          overview({
            filters: {
              ...overview().filters,
              decision: "pending",
            },
          }),
        ),
    });
    renderRoute(<OverviewPage />, { path: "/", url: "/?decision=pending" });

    await userEvent.click(await screen.findByRole("link", { name: "Crédito rural" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/processes?decision=pending&subject_code=4968&subject_name_exact=Cr%C3%A9dito+rural",
    );
  });
});
