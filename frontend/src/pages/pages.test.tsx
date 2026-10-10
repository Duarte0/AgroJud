import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { JobDetailPage } from "@/pages/job-detail-page";
import { ProcessDetailPage } from "@/pages/process-detail-page";
import { ProcessesPage } from "@/pages/processes-page";
import { RadarPage } from "@/pages/radar-page";
import { buildJob, buildPreset, buildProcess } from "@/test/factories";
import { apiError, json, mockApi, renderRoute } from "@/test/render";

afterEach(() => {
  vi.unstubAllGlobals();
});

const page = (items: unknown[], total = items.length) => ({
  items,
  page: 1,
  page_size: 25,
  total,
});

describe("ProcessesPage", () => {
  it("shows a read failure with retry instead of an empty list", async () => {
    let fail = true;
    const api = mockApi({
      "GET /api/v1/processes": () => {
        if (fail) throw new TypeError("Failed to fetch");
        return json(page([buildProcess()]));
      },
    });
    renderRoute(<ProcessesPage />, { path: "/processes", url: "/processes" });

    expect(await screen.findByText("Não foi possível carregar os processos")).toBeInTheDocument();
    expect(screen.queryByText(/Nenhum processo/)).not.toBeInTheDocument();

    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
    expect(await screen.findByText("0000001-00.2026.8.09.0001")).toBeInTheDocument();
    expect(api.count("POST", "/api/v1/jobs")).toBe(0);
  });

  it("distinguishes an empty filtered result and keeps filters from the URL", async () => {
    const api = mockApi({ "GET /api/v1/processes": () => json(page([])) });
    renderRoute(<ProcessesPage />, {
      path: "/processes",
      url: "/processes?subject=penhora&page=2",
    });

    expect(await screen.findByText("Nenhum processo corresponde aos filtros")).toBeInTheDocument();
    expect(screen.getByLabelText("Assunto")).toHaveValue("penhora");
    const query = new URL(api.calls[0]!.url).searchParams;
    expect(query.get("subject")).toBe("penhora");
    expect(query.get("page")).toBe("2");
  });

  it("keeps cached rows visible and flagged when a refresh fails", async () => {
    let fail = false;
    mockApi({
      "GET /api/v1/processes": () => {
        if (fail) return apiError(503, "database_unavailable", "Banco indisponível.");
        return json(page([buildProcess()]));
      },
    });
    const { client } = renderRoute(<ProcessesPage />, { path: "/processes", url: "/processes" });
    expect(await screen.findByText("0000001-00.2026.8.09.0001")).toBeInTheDocument();

    fail = true;
    await act(() => client.refetchQueries());
    expect(
      await screen.findByText(/Falha ao atualizar — exibindo dados possivelmente desatualizados/),
    ).toBeInTheDocument();
    expect(screen.getByText("0000001-00.2026.8.09.0001")).toBeInTheDocument();
  });

  it("applies the filter form to the URL and returns to the first page", async () => {
    mockApi({ "GET /api/v1/processes": () => json(page([buildProcess()])) });
    renderRoute(<ProcessesPage />, { path: "/processes", url: "/processes?page=3" });
    await screen.findByText("0000001-00.2026.8.09.0001");

    await userEvent.type(screen.getByLabelText("Classe"), "Execução");
    await userEvent.selectOptions(screen.getByLabelText("Decisão da triagem"), "relevant");
    await userEvent.selectOptions(screen.getByLabelText("Vínculo rural"), "confirmed");
    await userEvent.click(screen.getByRole("button", { name: "Pesquisar" }));
    await waitFor(() => {
      expect(screen.getByTestId("location")).toHaveTextContent("class=Execu%C3%A7%C3%A3o");
      expect(screen.getByTestId("location")).toHaveTextContent("decision=relevant");
      expect(screen.getByTestId("location")).toHaveTextContent("rural_link=confirmed");
      expect(screen.getByTestId("location")).not.toHaveTextContent("page=3");
    });
  });

  it("exports the active filters without pagination and shows the API limit error", async () => {
    let finishExport: (response: Response) => void = () => undefined;
    const api = mockApi({
      "GET /api/v1/processes": () => json(page([buildProcess()])),
      "GET /api/v1/exports/processes.csv": () =>
        new Promise<Response>((resolve) => {
          finishExport = resolve;
        }),
    });
    renderRoute(<ProcessesPage />, {
      path: "/processes",
      url: "/processes?subject=penhora&decision=relevant&page=3&page_size=1",
    });

    await screen.findByText("0000001-00.2026.8.09.0001");
    await userEvent.click(screen.getByRole("button", { name: "Exportar CSV" }));
    expect(screen.getByRole("button", { name: "Exportando…" })).toBeDisabled();

    const exportRequest = api.calls.find(
      (call) => new URL(call.url).pathname === "/api/v1/exports/processes.csv",
    );
    expect(exportRequest).toBeDefined();
    const params = new URL(exportRequest!.url).searchParams;
    expect(params.get("subject")).toBe("penhora");
    expect(params.get("decision")).toBe("relevant");
    expect(params.has("page")).toBe(false);
    expect(params.has("page_size")).toBe(false);

    await act(async () => {
      finishExport(
        apiError(
          422,
          "EXPORT_LIMIT_EXCEEDED",
          "A exportação contém mais processos que o limite. Restrinja os filtros.",
        ),
      );
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Não foi possível exportar os processos",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Restrinja os filtros");
    expect(screen.getByRole("button", { name: "Exportar CSV" })).toBeEnabled();
  });
});

describe("RadarPage", () => {
  it("sends a single POST on double click and opens the confirmed job", async () => {
    let resolvePost: (response: Response) => void = () => undefined;
    const api = mockApi({
      "GET /api/v1/presets": () =>
        json({
          environment: "demo",
          items: [
            buildPreset(),
            buildPreset({
              id: "sinal.penhora",
              name: "Candidato a sinal de penhora",
              availability: {
                environment: "demo",
                enabled: false,
                label: "demonstrativo sintético",
                reasons: ["Este item não é um preset disponível na demonstração sintética."],
              },
            }),
          ],
        }),
      "GET /api/v1/jobs": () => json(page([])),
      "POST /api/v1/jobs": () =>
        new Promise<Response>((resolve) => {
          resolvePost = resolve;
        }),
    });
    renderRoute(<RadarPage />, { path: "/radar", url: "/radar" });

    const disabled = await screen.findByRole("radio", { name: "Candidato a sinal de penhora" });
    expect(disabled).toBeDisabled();
    expect(
      screen.getByText("Este item não é um preset disponível na demonstração sintética."),
    ).toBeInTheDocument();

    const submit = screen.getByRole("button", { name: "Iniciar coleta" });
    await userEvent.dblClick(submit);
    expect(await screen.findByRole("button", { name: "Enviando…" })).toBeDisabled();
    expect(screen.getByTestId("location")).toHaveTextContent("/radar");

    resolvePost(
      json(
        {
          job_id: "11111111-1111-4111-8111-111111111111",
          collection_id: "22222222-2222-4222-8222-222222222222",
          status: "queued",
          reused: false,
        },
        202,
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/jobs/11111111-1111-4111-8111-111111111111",
      ),
    );
    expect(api.count("POST", "/api/v1/jobs")).toBe(1);
    const body = (await api.calls.find((call) => call.method === "POST")!.json()) as {
      criteria: Record<string, unknown>;
    };
    expect(body.criteria).toEqual({
      preset_id: "rural.credito_contratos",
      hit_budget: 2000,
      page_size: 100,
    });
  });

  it("disables submission when the environment source is unavailable", async () => {
    mockApi({
      "GET /api/v1/presets": () => json({ environment: "real", items: [buildPreset()] }),
      "GET /api/v1/jobs": () => json(page([])),
    });
    renderRoute(<RadarPage />, {
      path: "/radar",
      url: "/radar",
      environment: {
        environment: "real",
        source: "datajud",
        source_enabled: false,
        source_disabled_reason: "S1/S2 não aprovados.",
      },
    });
    expect(await screen.findByText("S1/S2 não aprovados.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Iniciar coleta" })).toBeDisabled();
  });
});

describe("JobDetailPage", () => {
  it("shows a 404 for a missing job", async () => {
    mockApi({
      "GET /api/v1/jobs/99999999-9999-4999-8999-999999999999": () =>
        apiError(404, "not_found", "O job solicitado não existe."),
    });
    renderRoute(<JobDetailPage />, {
      path: "/jobs/:jobId",
      url: "/jobs/99999999-9999-4999-8999-999999999999",
    });
    expect(await screen.findByRole("heading", { name: "Coleta não encontrada" })).toBeInTheDocument();
  });

  it("omits the percentage when the remote total is not exact", async () => {
    const job = buildJob({
      status: "partial",
      reason: "limit",
      coverage: {
        hits_confirmed: 100,
        budget_limit: 100,
        coverage: "partial",
        source_total: { present: true, value: 10_000, relation: "gte" },
      },
    });
    mockApi({ [`GET /api/v1/jobs/${job.id}`]: () => json(job) });
    renderRoute(<JobDetailPage />, { path: "/jobs/:jobId", url: `/jobs/${job.id}` });

    expect(await screen.findByTestId("no-percentage")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText(/pelo menos 10.000/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continuar coleta" })).toBeEnabled();
    expect(screen.getByTestId("polling-status")).toHaveTextContent("Atualização automática encerrada");
  });

  it("shows failure details without hiding persisted data", async () => {
    const job = buildJob({
      status: "failed",
      reason: "retry_exhausted",
      coverage: { hits_confirmed: 3, has_persisted_data: true, coverage: "partial" },
      attempts: [
        {
          id: "44444444-4444-4444-8444-444444444444",
          attempt_number: 5,
          started_at: "2026-10-09T10:00:00Z",
          finished_at: "2026-10-09T10:00:05Z",
          outcome: "failed",
          error_code: "source_timeout",
          error_summary: "A fonte não respondeu.",
        },
      ],
    });
    mockApi({ [`GET /api/v1/jobs/${job.id}`]: () => json(job) });
    renderRoute(<JobDetailPage />, { path: "/jobs/:jobId", url: `/jobs/${job.id}` });

    expect(await screen.findByText("A coleta falhou: Tentativas esgotadas")).toBeInTheDocument();
    expect(screen.getAllByText(/A fonte não respondeu\./).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Retomar coleta" })).toBeEnabled();
  });
});

describe("ProcessDetailPage", () => {
  const processId = "33333333-3333-4333-8333-333333333333";
  const representationId = "55555555-5555-4555-8555-555555555555";
  const diagnostic = {
    representation_id: representationId,
    available: true,
    is_complete: false,
    rejection_count: 1,
    normalizer_version: "v1",
    processed_at: "2026-10-09T10:00:00Z",
  };
  const movement = (overrides: Record<string, unknown>) => ({
    occurrence_id: crypto.randomUUID(),
    representation_id: representationId,
    content: { codigo: 26, nome: "Distribuído" },
    source_date: null,
    source_date_original: null,
    source_date_status: "missing",
    multiplicity_ordinal: 1,
    comparison_result: "FIRST_OBSERVED",
    comparison_detail: null,
    first_observed_at: "2026-10-09T13:00:00Z",
    ...overrides,
  });
  const unwatched = {
    process_id: processId,
    active: false,
    included_at: null,
    removed_at: null,
    history: [],
    baselines: [],
    last_refresh: null,
  };

  it("keeps origin, three dates, ambiguity and absent values explicit", async () => {
    mockApi({
      [`GET /api/v1/processes/${processId}`]: () => json(buildProcess()),
      [`GET /api/v1/processes/${processId}/watch`]: () => json(unwatched),
      [`GET /api/v1/processes/${processId}/triage-history`]: () => json(page([])),
      [`GET /api/v1/processes/${processId}/signals`]: () =>
        json({ process_id: processId, items: [], latest_run: null }),
      [`GET /api/v1/processes/${processId}/representations`]: () =>
        json(
          page([
            {
              id: representationId,
              process_id: processId,
              source: "datajud",
              tribunal: "TJGO",
              source_id: "TJGO_1_G1_0001",
              latest_version_id: null,
              class_code: "7",
              class_name: "Procedimento Comum",
              grau: "G1",
              court_unit_code: "123",
              court_unit_name: "1ª Vara Cível",
              source_filed_at: "2025-01-15T12:00:00Z",
              source_filed_at_original: "2025-01-15T09:00:00",
              source_filed_at_timezone_ambiguous: true,
              source_updated_at: null,
              source_updated_at_original: null,
              source_updated_at_timezone_ambiguous: false,
              last_observed_at: "2026-10-09T13:00:00Z",
              latest_collection: null,
              movement_diagnostic: diagnostic,
            },
          ]),
        ),
      [`GET /api/v1/processes/${processId}/movements`]: () =>
        json({
          process_id: processId,
          page: 1,
          page_size: 25,
          total: 2,
          diagnostics: [diagnostic],
          items: [
            movement({
              source_date: "2025-02-01T13:00:00Z",
              source_date_original: "2025-02-01T10:00:00",
              source_date_status: "timezone_ambiguous",
              multiplicity_ordinal: 2,
            }),
            movement({ content: { codigo: 51, nome: "Conclusão" } }),
          ],
        }),
    });
    renderRoute(<ProcessDetailPage />, {
      path: "/processes/:processId",
      url: `/processes/${processId}`,
    });

    const items = await screen.findAllByTestId("movement");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Ambígua: sem fuso na fonte");
    expect(items[0]).toHaveTextContent("original: 2025-02-01T10:00:00");
    expect(items[0]).toHaveTextContent("Ocorrência nº 2");
    expect(items[0]).toHaveTextContent("Origem: TJGO · G1 · 1ª Vara Cível");
    // Missing event date and missing source update are "Não informado", never today.
    expect(items[1]).toHaveTextContent("Data do eventoNão informado");
    expect(items[1]).toHaveTextContent("Atualização da fonteNão informado");
    expect(items[1]).toHaveTextContent("Observação local09/10/2026, 10:00");
    expect(screen.getByText("Há capas com lista de movimentos incompleta")).toBeInTheDocument();
    const representation = screen.getByTestId("representation");
    expect(representation).toHaveTextContent("Procedimento Comum (7)");
    expect(representation).toHaveTextContent("original: 2025-01-15T09:00:00");
  });

  it("reprocesses only the process explicitly open in its detail", async () => {
    let latestRun: unknown = null;
    let submitted: unknown;
    const api = mockApi({
      [`GET /api/v1/processes/${processId}`]: () => json(buildProcess({ id: processId })),
      [`GET /api/v1/processes/${processId}/watch`]: () => json(unwatched),
      [`GET /api/v1/processes/${processId}/triage-history`]: () => json(page([])),
      [`GET /api/v1/processes/${processId}/signals`]: () =>
        json({ process_id: processId, items: [], latest_run: latestRun }),
      [`GET /api/v1/processes/${processId}/representations`]: () => json(page([])),
      [`GET /api/v1/processes/${processId}/movements`]: () =>
        json({ process_id: processId, page: 1, page_size: 25, total: 0, diagnostics: [], items: [] }),
      "POST /api/v1/rule-runs": async (request) => {
        submitted = await request.json();
        latestRun = {
          job_id: "77777777-7777-4777-8777-777777777777",
          status: "queued",
          reason: null,
          coverage: null,
          process_count: 1,
          input_count: 1,
          processed_input_count: 0,
          completed_process_count: 0,
          stale_process_count: 0,
          not_evaluated_process_count: 0,
          resumable: false,
          created_at: "2026-10-09T12:00:00Z",
          finished_at: null,
        };
        return json({
          job_id: "77777777-7777-4777-8777-777777777777",
          status: "queued",
          process_count: 1,
          input_count: 1,
          reused: false,
        }, 202);
      },
    });
    renderRoute(<ProcessDetailPage />, {
      path: "/processes/:processId",
      url: `/processes/${processId}`,
    });

    await userEvent.click(await screen.findByRole("button", { name: "Reprocessar sinais deste processo" }));
    expect(await screen.findByText("Último reprocessamento: Na fila")).toBeInTheDocument();
    expect(submitted).toEqual({ process_ids: [processId] });
    expect(api.count("POST", "/api/v1/rule-runs")).toBe(1);
  });

  it("opens a signal's exact movement evidence from the process detail", async () => {
    const occurrenceId = "88888888-8888-4888-8888-888888888888";
    const signal = {
      id: "99999999-9999-4999-8999-999999999999",
      state: "current",
      evidence_stale: false,
      category: "penhora",
      rule_id: "sinal.penhora",
      rule_version: "1.0.0",
      rule_name: "Bloqueio, penhora ou arresto observado",
      explanation: "O código TPU 11382 foi observado.",
      environment: "demo",
      rule_enablement: {
        enabled: true,
        state: "synthetic_only",
        reason: "Fixture sintética local.",
        evidence_ids: ["fixture:signal-rules-v1"],
      },
      representation_id: representationId,
      evidence_kind: "movement_occurrence",
      evidence_id: occurrenceId,
      movement_occurrence_id: occurrenceId,
      evidence_version_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      evidence_snapshot_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      run_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
      evaluated_at: "2026-10-09T12:00:00Z",
    };
    mockApi({
      [`GET /api/v1/processes/${processId}`]: () => json(buildProcess({ id: processId })),
      [`GET /api/v1/processes/${processId}/watch`]: () => json(unwatched),
      [`GET /api/v1/processes/${processId}/triage-history`]: () => json(page([])),
      [`GET /api/v1/processes/${processId}/signals`]: () =>
        json({ process_id: processId, items: [signal], latest_run: null }),
      [`GET /api/v1/processes/${processId}/representations`]: () => json(page([])),
      [`GET /api/v1/processes/${processId}/movements`]: (request) => {
        const evidenceId = new URL(request.url).searchParams.get("occurrence_id");
        const item = {
          occurrence_id: occurrenceId,
          representation_id: representationId,
          content: { codigo: 11382, nome: "Bloqueio, Penhora ou Arresto" },
          source_date: null,
          source_date_original: null,
          source_date_status: "missing",
          multiplicity_ordinal: 1,
          comparison_result: "FIRST_OBSERVED",
          comparison_detail: null,
          first_observed_at: "2026-10-09T12:00:00Z",
        };
        return json({
          process_id: processId,
          page: 1,
          page_size: 25,
          total: evidenceId === occurrenceId ? 1 : 0,
          diagnostics: [],
          items: evidenceId === occurrenceId ? [item] : [],
        });
      },
    });
    renderRoute(<ProcessDetailPage />, {
      path: "/processes/:processId",
      url: `/processes/${processId}`,
    });

    await userEvent.click(await screen.findByRole("link", { name: "Ver evidência na linha do tempo" }));
    expect(await screen.findByText("Bloqueio, Penhora ou Arresto")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(`evidence=${occurrenceId}`);
  });
});
