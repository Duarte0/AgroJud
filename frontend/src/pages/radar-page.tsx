import { Info, Loader2, Play } from "lucide-react";
import { useId, useRef, useState, type SubmitEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { describeError } from "@/api/client";
import { useCreateJob, useJobList, usePresets } from "@/api/queries";
import type { Preset } from "@/api/types";
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
  // Blocks a second submit before React re-renders the disabled button.
  const submitting = useRef(false);

  const suggested = defaultWindow();
  const [useDefaultWindow, setUseDefaultWindow] = useState(true);
  const [filedFrom, setFiledFrom] = useState(suggested.from);
  const [filedThrough, setFiledThrough] = useState(suggested.through);
  const [hitBudget, setHitBudget] = useState(String(MAX_HIT_BUDGET));
  const [validation, setValidation] = useState<string | null>(null);

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
