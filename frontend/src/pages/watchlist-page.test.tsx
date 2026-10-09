import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProcessWatchPanel } from "@/components/process-watch-panel";
import { WatchlistPage } from "@/pages/watchlist-page";
import { json, mockApi, renderRoute } from "@/test/render";

const processId = "33333333-3333-4333-8333-333333333333";
const jobId = "11111111-1111-4111-8111-111111111111";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WatchlistPage", () => {
  it("shows the query outcome and opens the accepted refresh job", async () => {
    const api = mockApi({
      "GET /api/v1/watchlist": () =>
        json({
          items: [
            {
              process_id: processId,
              numero_cnj: "00000010020268090001",
              included_at: "2026-10-09T10:00:00Z",
              last_refresh: {
                job_id: jobId,
                state: "absent_in_query",
                job_status: "completed",
                checked_at: "2026-10-09T11:00:00Z",
                hit_count: 0,
              },
            },
          ],
          page: 1,
          page_size: 25,
          total: 1,
        }),
      [`POST /api/v1/processes/${processId}/refresh`]: () =>
        json(
          {
            job_id: jobId,
            collection_id: "22222222-2222-4222-8222-222222222222",
            status: "queued",
            reused: false,
          },
          202,
        ),
    });
    renderRoute(<WatchlistPage />, { path: "/watchlist", url: "/watchlist" });

    expect(await screen.findByText("Ausente nesta consulta")).toBeInTheDocument();
    expect(screen.queryByText(/inexistente/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Atualizar" }));

    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(`/jobs/${jobId}`),
    );
    expect(api.count("POST", `/api/v1/processes/${processId}/refresh`)).toBe(1);
  });

  it("removes an entry from the active list without presenting removal as cancellation", async () => {
    let active = true;
    const api = mockApi({
      "GET /api/v1/watchlist": () =>
        json({
          items: active
            ? [
                {
                  process_id: processId,
                  numero_cnj: "00000010020268090001",
                  included_at: "2026-10-09T10:00:00Z",
                  last_refresh: {
                    job_id: jobId,
                    state: "pending",
                    job_status: "running",
                    checked_at: "2026-10-09T11:00:00Z",
                    hit_count: null,
                  },
                },
              ]
            : [],
          page: 1,
          page_size: 25,
          total: active ? 1 : 0,
        }),
      [`DELETE /api/v1/processes/${processId}/watch`]: () => {
        active = false;
        return json({
          process_id: processId,
          active: false,
          included_at: "2026-10-09T10:00:00Z",
          removed_at: "2026-10-09T12:00:00Z",
          history: [],
          last_refresh: {
            job_id: jobId,
            state: "pending",
            job_status: "running",
            checked_at: "2026-10-09T11:00:00Z",
            hit_count: null,
          },
        });
      },
    });
    renderRoute(<WatchlistPage />, { path: "/watchlist", url: "/watchlist" });

    await screen.findByText("Atualização em andamento");
    await userEvent.click(screen.getByRole("button", { name: "Remover" }));
    expect(await screen.findByText("Nenhum processo acompanhado")).toBeInTheDocument();
    expect(api.count("DELETE", `/api/v1/processes/${processId}/watch`)).toBe(1);
    expect(api.count("POST", `/api/v1/jobs/${jobId}/cancel`)).toBe(0);
  });
});

describe("ProcessWatchPanel", () => {
  it("includes the local process and displays its audit history", async () => {
    let active = false;
    let includeCount = 0;
    const watch = () => ({
      process_id: processId,
      active,
      included_at: active ? "2026-10-09T10:00:00Z" : null,
      removed_at: null,
      history: active
        ? [
            {
              id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
              action: "included",
              created_at: "2026-10-09T10:00:00Z",
            },
          ]
        : [],
      last_refresh: null,
    });
    mockApi({
      [`GET /api/v1/processes/${processId}/watch`]: () => json(watch()),
      [`PUT /api/v1/processes/${processId}/watch`]: () => {
        active = true;
        includeCount += 1;
        return json(watch());
      },
    });
    renderRoute(<ProcessWatchPanel processId={processId} />, {
      path: "/processes/:processId",
      url: `/processes/${processId}`,
    });

    await userEvent.click(await screen.findByRole("button", { name: "Acompanhar processo" }));
    expect(await screen.findByText("Acompanhamento ativo")).toBeInTheDocument();
    expect(screen.getByTestId("watch-history-entry")).toHaveTextContent("Incluído em");
    expect(includeCount).toBe(1);
  });
});
