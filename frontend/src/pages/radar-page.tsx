import { Info, Loader2, Play } from "lucide-react";
import { useId, useRef, useState, type SubmitEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { describeError } from "@/api/client";
import {
  useCreateJob,
  useCreateSavedSearch,
  useJobList,
  usePatchSavedSearch,
  usePresets,
  useRunSavedSearch,
  useSavedSearches,
} from "@/api/queries";
import type { Preset, SavedSearch } from "@/api/types";
import { useCurrentEnvironment } from "@/app/environment-context";
import { JobStatusBadge } from "@/components/job-status-badge";
import { PageHeader } from "@/components/page-header";
import { usePageTitle } from "@/hooks/use-page-title";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleNotice,
} from "@/components/query-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { defaultWindow, DEFAULT_WINDOW_MONTHS } from "@/lib/date-window";
import { formatDateTime, jobKindLabel, ruralLinkLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

const MAX_HIT_BUDGET = 2_000;
const PAGE_SIZE = 100;

function formatScheduleTime(value: string): string {
  return formatDateTime(value);
}

function savedSearchScheduleLabel(search: SavedSearch): string {
  const dispatch = search.last_dispatch;
  if (!dispatch) return "Nenhum disparo realizado ainda.";
  if (dispatch.status === "pending") return "Aguardando o job anterior deste alvo.";
  if (dispatch.status === "coalesced") return "Disparo agregado a uma avaliação pendente.";
  if (dispatch.status === "blocked") return `Não executado: ${dispatch.reason ?? "validação pendente."}`;
  if (dispatch.status === "cancelled") return "Disparo cancelado porque a busca foi desativada.";
  return dispatch.job_id ? `Job ${dispatch.job_id}` : "Disparo enfileirado.";
}

function SavedSearchRow({ search }: { search: SavedSearch }) {
  const navigate = useNavigate();
  const patch = usePatchSavedSearch();
  const run = useRunSavedSearch();
  const actionError = patch.error ?? run.error;

  return (
    <li className="space-y-3 py-4" data-testid="saved-search" data-search-id={search.id}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h3 className="font-medium">{search.name}</h3>
          <p className="text-xs text-muted-foreground">
            {search.preset_id} · preset {search.preset_version} · busca v{search.version}
          </p>
          <p className="text-sm text-muted-foreground">
            {search.window_mode === "rolling_12_months"
              ? "Janela móvel de 12 meses"
              : `Período fixo: ${search.filters.filed_from} a ${search.filters.filed_through}`}
          </p>
          <p className="text-sm">
            {search.enabled ? "Agenda ativa" : "Agenda desativada"}
            {search.next_run_at ? ` · próxima atualização ${formatScheduleTime(search.next_run_at)}` : ""}
          </p>
          <p className="text-xs text-muted-foreground">{savedSearchScheduleLabel(search)}</p>
          {search.last_dispatch?.missed_from ? (
            <p className="text-xs text-warning-foreground">
              Intervalo perdido: {search.last_dispatch.missed_from} a {search.last_dispatch.missed_through}
            </p>
          ) : null}
          {!search.availability.enabled ? (
            <div className="space-y-1 text-sm text-warning-foreground" role="status">
              {search.availability.reasons.map((reason) => <p key={reason}>{reason}</p>)}
            </div>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            onClick={() => patch.mutate({ id: search.id, body: { enabled: !search.enabled } })}
            disabled={patch.isPending}
          >
            {search.enabled ? "Desativar" : "Ativar"}
          </Button>
          <Button
            onClick={() =>
              run.mutate(search.id, {
                onSuccess: (created) =>
                  void navigate(`/jobs/${created.job_id}`, {
                    state: { created: true, reused: created.reused },
                  }),
              })
            }
            disabled={!search.availability.enabled || run.isPending}
          >
            {run.isPending ? "Enfileirando…" : "Executar agora"}
          </Button>
        </div>
      </div>
      {actionError ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>A ação não foi concluída</AlertTitle>
          <AlertDescription>{describeError(actionError)}</AlertDescription>
        </Alert>
      ) : null}
    </li>
  );
}

function SavedSearchesPanel() {
  const searches = useSavedSearches();
  return (
    <section className="mt-8 border-t pt-6" aria-labelledby="saved-searches-title">
      <div className="mb-3">
        <h2 id="saved-searches-title" className="text-lg font-semibold">Buscas salvas</h2>
        <p className="text-sm text-muted-foreground">
          Atualizações automáticas ocorrem às 06h no fuso de São Paulo. Executar agora é uma ação manual.
        </p>
      </div>
      {searches.isPending ? (
        <LoadingState label="Carregando buscas salvas…" />
      ) : !searches.data ? (
        <ErrorState
          title="Não foi possível carregar as buscas salvas"
          error={searches.error}
          onRetry={() => void searches.refetch()}
          retrying={searches.isFetching}
        />
      ) : searches.data.length === 0 ? (
        <EmptyState title="Nenhuma busca salva">
          Salve os critérios do radar para ativar uma atualização diária.
        </EmptyState>
      ) : (
        <ul className="divide-y">
          {searches.data.map((search) => <SavedSearchRow key={search.id} search={search} />)}
        </ul>
      )}
    </section>
  );
}

function PresetOption({
  preset,
  checked,
  onSelect,
}: {
  preset: Preset;
  checked: boolean;
  onSelect: () => void;
}) {
  const id = useId();
  const enabled = preset.availability.enabled;
  return (
    <div
      className={cn(
        "rounded-lg border p-4 transition-colors",
        checked && "border-primary bg-accent",
        !enabled && "bg-muted/60",
      )}
      data-preset={preset.id}
      data-enabled={enabled}
    >
      <div className="flex items-start gap-3">
        <input
          id={id}
          type="radio"
          name="preset"
          value={preset.id}
          checked={checked}
          disabled={!enabled}
          onChange={onSelect}
          aria-describedby={`${id}-details`}
          className="mt-1 size-4 accent-primary disabled:cursor-not-allowed"
        />
        <div className="min-w-0 flex-1">
          <label htmlFor={id} className={cn("font-medium", enabled && "cursor-pointer")}>
            {preset.name}
          </label>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <Badge variant="outline" className="max-w-full whitespace-normal wrap-anywhere">
              {preset.id} · versão {preset.version}
            </Badge>
            <Badge variant="secondary" className="max-w-full whitespace-normal">{ruralLinkLabel(preset.rural_link_status)}</Badge>
            <Badge variant={enabled ? "success" : "outline"} className="max-w-full whitespace-normal">{preset.availability.label}</Badge>
          </div>
          <div id={`${id}-details`} className="mt-2 space-y-1 text-sm text-muted-foreground">
            <p>{preset.capture_explanation}</p>
            {enabled ? (
              preset.availability.reasons.map((reason) => <p key={reason}>{reason}</p>)
            ) : (
              <div>
                <p className="font-medium text-foreground">Indisponível neste ambiente:</p>
                <ul className="list-disc pl-5">
                  {preset.availability.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CollectionForm({ presets }: { presets: Preset[] }) {
  const environment = useCurrentEnvironment();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const createJob = useCreateJob();
  const createSavedSearch = useCreateSavedSearch();
  // Blocks a second submit before React re-renders the disabled button.
  const submitting = useRef(false);

  const suggested = defaultWindow();
  const [useDefaultWindow, setUseDefaultWindow] = useState(true);
  const [filedFrom, setFiledFrom] = useState(suggested.from);
  const [filedThrough, setFiledThrough] = useState(suggested.through);
  const [hitBudget, setHitBudget] = useState(String(MAX_HIT_BUDGET));
  const [validation, setValidation] = useState<string | null>(null);
  const [savedSearchName, setSavedSearchName] = useState("");
  const [enableSavedSearch, setEnableSavedSearch] = useState(false);

  const requestedId = searchParams.get("preset");
  const enabledPresets = presets.filter((preset) => preset.availability.enabled);
  const selected =
    enabledPresets.find((preset) => preset.id === requestedId) ?? enabledPresets[0] ?? null;
  const sourceBlocked = !environment.source_enabled;

  function selectPreset(id: string) {
    const next = new URLSearchParams(searchParams);
    next.set("preset", id);
    setSearchParams(next, { replace: true });
  }

  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || !selected || sourceBlocked) return;
    const budget = Number(hitBudget);
    if (!Number.isInteger(budget) || budget < 1 || budget > MAX_HIT_BUDGET) {
      setValidation(`O limite deve ser um número inteiro entre 1 e ${MAX_HIT_BUDGET}.`);
      return;
    }
    if (!useDefaultWindow && (!filedFrom || !filedThrough || filedFrom > filedThrough)) {
      setValidation("Informe uma janela válida: a data inicial deve ser anterior ou igual à final.");
      return;
    }
    setValidation(null);
    submitting.current = true;
    try {
      const created = await createJob.mutateAsync({
        kind: "discovery",
        criteria: {
          preset_id: selected.id,
          hit_budget: budget,
          page_size: PAGE_SIZE,
          ...(useDefaultWindow ? {} : { filed_from: filedFrom, filed_through: filedThrough }),
        },
      });
      navigate(`/jobs/${created.job_id}`, { state: { created: true, reused: created.reused } });
    } catch {
      // The mutation error is rendered below; nothing was started on our side.
    } finally {
      submitting.current = false;
    }
  }

  async function saveSearch() {
    if (!selected || sourceBlocked) return;
    const budget = Number(hitBudget);
    if (!savedSearchName.trim()) {
      setValidation("Informe um nome para a busca salva.");
      return;
    }
    if (!Number.isInteger(budget) || budget < 1 || budget > MAX_HIT_BUDGET) {
      setValidation(`O limite deve ser um número inteiro entre 1 e ${MAX_HIT_BUDGET}.`);
      return;
    }
    if (!useDefaultWindow && (!filedFrom || !filedThrough || filedFrom > filedThrough)) {
      setValidation("Informe uma janela válida: a data inicial deve ser anterior ou igual à final.");
      return;
    }
    setValidation(null);
    try {
      await createSavedSearch.mutateAsync({
        name: savedSearchName.trim(),
        preset_id: selected.id,
        window_mode: useDefaultWindow ? "rolling_12_months" : "fixed",
        filters: {
          page_size: PAGE_SIZE,
          hit_budget: budget,
          ...(useDefaultWindow ? {} : { filed_from: filedFrom, filed_through: filedThrough }),
        },
        enabled: enableSavedSearch,
      });
      setSavedSearchName("");
    } catch {
      // The mutation error is rendered below; no search was saved on our side.
    }
  }

  const pending = createJob.isPending;

  return (
    <form onSubmit={submit} className="space-y-6" aria-describedby="collection-form-help">
      <p id="collection-form-help" className="text-sm text-muted-foreground">
        A coleta é enfileirada e processada pelo worker. O acompanhamento mostra apenas o estado
        persistido.
      </p>

      {sourceBlocked ? (
        <Alert variant="warning">
          <Info aria-hidden="true" />
          <AlertTitle>Coleta indisponível neste ambiente</AlertTitle>
          <AlertDescription>
            {environment.source_disabled_reason ?? "A fonte está desabilitada."}
          </AlertDescription>
        </Alert>
      ) : null}

      <fieldset className="space-y-3">
        <legend className="mb-2 text-sm font-semibold">Preset temático</legend>
        {presets.map((preset) => (
          <PresetOption
            key={preset.id}
            preset={preset}
            checked={selected?.id === preset.id}
            onSelect={() => selectPreset(preset.id)}
          />
        ))}
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="mb-2 text-sm font-semibold">Janela de ajuizamento</legend>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={useDefaultWindow}
            onChange={(event) => setUseDefaultWindow(event.target.checked)}
            className="mt-0.5 size-4 accent-primary"
          />
          <span>
            Usar a janela padrão: últimos {DEFAULT_WINDOW_MONTHS} meses, calculada pelo servidor no
            fuso America/Sao_Paulo ao criar a coleta.
          </span>
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="filed-from">Ajuizados a partir de</Label>
            <Input
              id="filed-from"
              type="date"
              value={filedFrom}
              onChange={(event) => setFiledFrom(event.target.value)}
              disabled={useDefaultWindow}
              required={!useDefaultWindow}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="filed-through">Ajuizados até (inclusive)</Label>
            <Input
              id="filed-through"
              type="date"
              value={filedThrough}
              onChange={(event) => setFiledThrough(event.target.value)}
              disabled={useDefaultWindow}
              required={!useDefaultWindow}
            />
          </div>
        </div>
      </fieldset>

      <div className="space-y-1.5 sm:max-w-xs">
        <Label htmlFor="hit-budget">Limite de registros desta execução</Label>
        <Input
          id="hit-budget"
          type="number"
          inputMode="numeric"
          min={1}
          max={MAX_HIT_BUDGET}
          step={1}
          value={hitBudget}
          onChange={(event) => setHitBudget(event.target.value)}
          aria-describedby="hit-budget-help"
          required
        />
        <p id="hit-budget-help" className="text-xs text-muted-foreground">
          Máximo {MAX_HIT_BUDGET}; páginas de {PAGE_SIZE}. Ao atingir o limite a coleta fica
          parcial e pode ser continuada.
        </p>
      </div>

      {validation ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {validation}
        </p>
      ) : null}
      {createJob.isError ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>A coleta não foi iniciada</AlertTitle>
          <AlertDescription>{describeError(createJob.error)}</AlertDescription>
        </Alert>
      ) : null}

      <div className="space-y-3 rounded-lg border p-4">
        <div>
          <h2 className="font-semibold">Salvar estes critérios</h2>
          <p className="text-sm text-muted-foreground">
            A busca mantém a versão do preset e os filtros desta coleta.
          </p>
        </div>
        <div className="space-y-1.5 sm:max-w-md">
          <Label htmlFor="saved-search-name">Nome da busca salva</Label>
          <Input
            id="saved-search-name"
            value={savedSearchName}
            maxLength={160}
            onChange={(event) => setSavedSearchName(event.target.value)}
            placeholder="Ex.: Crédito rural no TJGO"
          />
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={enableSavedSearch}
            onChange={(event) => setEnableSavedSearch(event.target.checked)}
            className="mt-0.5 size-4 accent-primary"
          />
          <span>Ativar atualização diária às 06h</span>
        </label>
        {createSavedSearch.isError ? (
          <Alert variant="destructive" role="alert">
            <AlertTitle>A busca não foi salva</AlertTitle>
            <AlertDescription>{describeError(createSavedSearch.error)}</AlertDescription>
          </Alert>
        ) : null}
        {createSavedSearch.isSuccess ? (
          <p role="status" className="text-sm text-success">Busca salva.</p>
        ) : null}
        <Button type="button" variant="outline" onClick={() => void saveSearch()} disabled={createSavedSearch.isPending || !selected || sourceBlocked}>
          {createSavedSearch.isPending ? "Salvando…" : "Salvar busca"}
        </Button>
      </div>

      <Button type="submit" size="lg" disabled={pending || !selected || sourceBlocked}>
        {pending ? (
          <Loader2 className="animate-spin" aria-hidden="true" />
        ) : (
          <Play aria-hidden="true" />
        )}
        {pending ? "Enviando…" : "Iniciar coleta"}
      </Button>
    </form>
  );
}

function RecentJobs() {
  const jobs = useJobList({ page: 1, page_size: 5 });
  if (jobs.isPending) return <LoadingState label="Carregando coletas recentes…" />;
  if (!jobs.data) {
    return (
      <ErrorState
        title="Não foi possível carregar as coletas recentes"
        error={jobs.error}
        onRetry={() => void jobs.refetch()}
        retrying={jobs.isFetching}
      />
    );
  }
  return (
    <div className="space-y-3">
      <StaleNotice query={jobs} />
      {jobs.data.items.length === 0 ? (
        <EmptyState title="Nenhuma coleta registrada">
          Inicie uma coleta para acompanhar seu progresso aqui.
        </EmptyState>
      ) : (
        <ul className="divide-y">
          {jobs.data.items.map((job) => (
            <li key={job.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <Link to={`/jobs/${job.id}`} className="text-sm font-medium underline-offset-4 hover:underline">
                {jobKindLabel(job.kind)} · {formatDateTime(job.created_at)}
              </Link>
              <JobStatusBadge status={job.status} />
            </li>
          ))}
        </ul>
      )}
      <Link to="/jobs" className="text-sm text-primary underline-offset-4 hover:underline">
        Ver todas as coletas
      </Link>
    </div>
  );
}

export function RadarPage() {
  usePageTitle("Radar");
  const presets = usePresets();

  return (
    <>
      <PageHeader
        title="Radar"
        description="Escolha um preset versionado, ajuste a janela e o limite, e inicie uma coleta no TJGO."
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Nova coleta por preset</CardTitle>
            <CardDescription>
              Itens sem evidência suficiente aparecem desabilitados com o motivo.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {presets.isPending ? (
              <LoadingState label="Carregando presets…" />
            ) : presets.data ? (
              <>
                <StaleNotice query={presets} />
                <CollectionForm presets={presets.data.items} />
                <SavedSearchesPanel />
              </>
            ) : (
              <ErrorState
                title="Não foi possível carregar os presets"
                error={presets.error}
                onRetry={() => void presets.refetch()}
                retrying={presets.isFetching}
              />
            )}
          </CardContent>
        </Card>
        <Card className="self-start">
          <CardHeader>
            <CardTitle>Coletas recentes</CardTitle>
          </CardHeader>
          <CardContent>
            <RecentJobs />
          </CardContent>
        </Card>
      </div>
    </>
  );
}
