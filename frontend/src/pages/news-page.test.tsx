import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { NewsPage } from "@/pages/news-page";
import type { ProcessNews } from "@/api/types";
import { json, mockApi, renderRoute } from "@/test/render";

const newsId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const processId = "33333333-3333-4333-8333-333333333333";

function buildNews(status: ProcessNews["status"] = "pending"): ProcessNews {
  return {
    id: newsId,
    process_id: processId,
    numero_cnj: "00000010020268090001",
    representation_id: "55555555-5555-4555-8555-555555555555",
    source: "synthetic",
    tribunal: "TJGO",
    source_id: "demo-e2e",
    category: "NEW_OBSERVATION",
    status,
    event_date: "2020-04-01T03:04:05Z",
    event_date_original: "2020-04-01T03:04:05Z",
    event_date_status: "timezone_aware",
    first_observed_at: "2026-10-09T12:00:00Z",
    evidence: {
      content: { codigo: 26, nome: "Movimento observado" },
    },
    provenance: "ingestion",
    created_at: "2026-10-09T12:00:00Z",
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NewsPage", () => {
  it("shows both dates and lets the operator review and reopen a novelty", async () => {
    let status: ProcessNews["status"] = "pending";
    const api = mockApi({
      "GET /api/v1/news": () =>
        json({ items: [buildNews(status)], page: 1, page_size: 25, total: 1 }),
      [`PATCH /api/v1/news/${newsId}`]: async (request) => {
        const body = (await request.json()) as { status: ProcessNews["status"] };
        status = body.status;
        return json(buildNews(status));
      },
    });
    renderRoute(<NewsPage />, { path: "/news", url: "/news" });

    const item = await screen.findByTestId("news-item");
    expect(within(item).getByText("Conteúdo recém-observado")).toBeInTheDocument();
    expect(within(item).getByText("01/04/2020, 00:04")).toBeInTheDocument();
    expect(within(item).getByText("09/10/2026, 09:00")).toBeInTheDocument();
    expect(
      within(item).getByText(/isso não afirma que seja um novo ato jurídico/),
    ).toBeInTheDocument();

    await userEvent.click(within(item).getByRole("button", { name: "Marcar como revisada" }));
    await waitFor(() => expect(within(item).getByText("Revisada")).toBeInTheDocument());
    await userEvent.click(within(item).getByRole("button", { name: "Reabrir revisão" }));
    await waitFor(() => expect(within(item).getByText("Pendente")).toBeInTheDocument());
    expect(api.count("PATCH", `/api/v1/news/${newsId}`)).toBe(2);
  });

  it("loads URL filters and resets pagination when the process filter changes", async () => {
    const api = mockApi({
      "GET /api/v1/news": () => json({ items: [], page: 2, page_size: 25, total: 40 }),
    });
    renderRoute(<NewsPage />, {
      path: "/news",
      url: "/news?status=pending&category=NEW_OBSERVATION&page=2",
    });

    await screen.findByText("Nenhuma novidade neste recorte");
    const query = new URL(api.calls[0]!.url).searchParams;
    expect(query.get("status")).toBe("pending");
    expect(query.get("category")).toBe("NEW_OBSERVATION");
    expect(query.get("page")).toBe("2");

    await userEvent.type(screen.getByLabelText("Processo por número CNJ"), "0000001-00.2026.8.09.0001");
    await userEvent.click(screen.getByRole("button", { name: "Filtrar" }));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("process_number=");
      expect(screen.getByTestId("location")).not.toHaveTextContent("page=2");
    });
  });
});
