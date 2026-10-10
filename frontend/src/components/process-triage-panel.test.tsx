import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProcessTriagePanel, ProcessTriageHistory } from "@/components/process-triage-panel";
import type { ProcessTriage } from "@/api/types";
import { buildProcess } from "@/test/factories";
import { apiError, json, mockApi, renderRoute } from "@/test/render";

afterEach(() => {
  vi.unstubAllGlobals();
});

const processId = "33333333-3333-4333-8333-333333333333";
const initial: ProcessTriage = {
  decision: "pending",
  rural_link: "unconfirmed",
  note: "",
  version: 0,
  updated_at: null,
};
const emptyHistory = { items: [], page: 1, page_size: 20, total: 0 };

describe("ProcessTriagePanel", () => {
  it("saves an independent decision and renders plain notes as escaped text", async () => {
    let current = initial;
    const history: unknown[] = [];
    const api = mockApi({
      [`GET /api/v1/processes/${processId}`]: () =>
        json(buildProcess({ triage: current })),
      [`GET /api/v1/processes/${processId}/triage-history`]: () =>
        json({ ...emptyHistory, items: history, total: history.length }),
      [`PATCH /api/v1/processes/${processId}/triage`]: async (request) => {
        const body = (await request.json()) as {
          expected_version: number;
          decision?: ProcessTriage["decision"];
          rural_link?: ProcessTriage["rural_link"];
          note?: string;
        };
        expect(body.expected_version).toBe(current.version);
        const previous = current;
        current = {
          decision: body.decision ?? current.decision,
          rural_link: body.rural_link ?? current.rural_link,
          note: body.note ?? current.note,
          version: current.version + 1,
          updated_at: "2026-10-09T13:00:00Z",
        };
        history.unshift({
          id: "55555555-5555-4555-8555-555555555555",
          version: current.version,
          previous_state: previous,
          new_state: current,
          origin: "manual",
          created_at: current.updated_at,
        });
        return json(current);
      },
    });
    renderRoute(<><ProcessTriagePanel processId={processId} initialTriage={initial} /><ProcessTriageHistory processId={processId} /></>, {
      path: "/processes/:processId",
      url: `/processes/${processId}`,
    });

    await screen.findByText("Nenhuma alteração humana registrada.");
    await userEvent.selectOptions(screen.getByLabelText("Decisão"), "relevant");
    await userEvent.type(screen.getByLabelText("Nota de revisão"), "<script>alert(1)</script>");
    await userEvent.click(screen.getByRole("button", { name: "Salvar triagem" }));

    expect(await screen.findByTestId("triage-history-entry")).toHaveTextContent("Relevante");
    expect(screen.getByLabelText("Nota de revisão")).toHaveValue("<script>alert(1)</script>");
    expect(screen.getAllByText("<script>alert(1)</script>")).toHaveLength(2);
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(api.count("PATCH", `/api/v1/processes/${processId}/triage`)).toBe(1);
    expect(current.rural_link).toBe("unconfirmed");
    expect(current.version).toBe(1);
  });

  it("keeps the draft through a 409 and reloads the new server version", async () => {
    let current = initial;
    let patchCount = 0;
    let failReload = false;
    const patchBodies: Array<Record<string, unknown>> = [];
    mockApi({
      [`GET /api/v1/processes/${processId}`]: () =>
        failReload
          ? apiError(503, "upstream_unavailable", "A API falhou ao recarregar.")
          : json(buildProcess({ triage: current })),
      [`GET /api/v1/processes/${processId}/triage-history`]: () => json(emptyHistory),
      [`PATCH /api/v1/processes/${processId}/triage`]: async (request) => {
        const body = (await request.json()) as Record<string, unknown>;
        patchBodies.push(body);
        patchCount += 1;
        if (patchCount === 1) {
          current = {
            decision: "relevant",
            rural_link: "unconfirmed",
            note: "nota salva na outra aba",
            version: 1,
            updated_at: "2026-10-09T13:00:00Z",
          };
          return apiError(409, "conflict", "A triagem foi alterada em outra edição.");
        }
        expect(body.expected_version).toBe(1);
        current = {
          decision: body.decision as ProcessTriage["decision"],
          rural_link: current.rural_link,
          note: body.note as string,
          version: 2,
          updated_at: "2026-10-09T13:05:00Z",
        };
        return json(current);
      },
    });
    renderRoute(<><ProcessTriagePanel processId={processId} initialTriage={initial} /><ProcessTriageHistory processId={processId} /></>, {
      path: "/processes/:processId",
      url: `/processes/${processId}`,
    });

    await screen.findByText("Nenhuma alteração humana registrada.");
    await userEvent.selectOptions(screen.getByLabelText("Decisão"), "discarded");
    await userEvent.type(screen.getByLabelText("Nota de revisão"), "rascunho da primeira aba");
    await userEvent.click(screen.getByRole("button", { name: "Salvar triagem" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("mudou em outra edição");
    expect(screen.getByLabelText("Nota de revisão")).toHaveValue("rascunho da primeira aba");

    failReload = true;
    await userEvent.click(screen.getByRole("button", { name: "Recarregar dados" }));
    expect(
      await screen.findByText("O banco de dados está temporariamente indisponível."),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("mudou em outra edição");
    expect(screen.getByLabelText("Nota de revisão")).toHaveValue("rascunho da primeira aba");

    failReload = false;
    await userEvent.click(screen.getByRole("button", { name: "Recarregar dados" }));
    await waitFor(() => expect(screen.getByText(/Confira a versão 1/)).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Decisão")).toHaveValue("discarded");
    expect(screen.getByLabelText("Nota de revisão")).toHaveValue("rascunho da primeira aba");
    await userEvent.click(screen.getByRole("button", { name: "Salvar triagem" }));

    await waitFor(() => expect(current.version).toBe(2));
    expect(patchBodies[0]).toMatchObject({
      expected_version: 0,
      decision: "discarded",
      note: "rascunho da primeira aba",
    });
    expect(patchBodies[1]).toMatchObject({
      expected_version: 1,
      decision: "discarded",
      note: "rascunho da primeira aba",
    });
  });

  it("requires a justification before confirming a rural link", async () => {
    const api = mockApi({
      [`GET /api/v1/processes/${processId}`]: () => json(buildProcess()),
      [`GET /api/v1/processes/${processId}/triage-history`]: () => json(emptyHistory),
    });
    renderRoute(<><ProcessTriagePanel processId={processId} initialTriage={initial} /><ProcessTriageHistory processId={processId} /></>, {
      path: "/processes/:processId",
      url: `/processes/${processId}`,
    });

    await screen.findByText("Nenhuma alteração humana registrada.");
    await userEvent.selectOptions(screen.getByLabelText("Vínculo rural"), "confirmed");
    await userEvent.click(screen.getByRole("button", { name: "Salvar triagem" }));
    expect(await screen.findByText("Informe uma nota não vazia para confirmar o vínculo rural.")).toBeInTheDocument();
    expect(api.count("PATCH", `/api/v1/processes/${processId}/triage`)).toBe(0);
  });
});
