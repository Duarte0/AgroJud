import { Info, Loader2, Play } from "lucide-react";
import { useId, useRef, useState, type SubmitEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { describeError } from "@/api/client";
import {
  useCreateJobGroup,
  useCreateSavedSearch,
  useJobList,
  usePatchSavedSearch,
  usePresets,
  useRunSavedSearch,
  useSavedSearches,
  useTribunalAvailability,
} from "@/api/queries";
import type { Preset, SavedSearch } from "@/api/types";
import { useCurrentEnvironment } from "@/app/environment-context";
import { TribunalSelector } from "@/components/tribunal-selector";
import { JobStatusBadge } from "@/components/job-status-badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { defaultWindow, DEFAULT_WINDOW_MONTHS } from "@/lib/date-window";
import { formatDateTime, jobKindLabel, ruralLinkLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

const MIN_PAGE_SIZE = 10;
const MAX_PAGE_SIZE = 10_000;
const DEFAULT_PAGE_SIZE = 5_000;

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
  if (dispatch.group_id) return `Grupo ${dispatch.group_id}`;
  return dispatch.job_id ? `Job ${dispatch.job_id}` : "Disparo enfileirado.";
}

function SavedSearchEditForm({
  search,
  presets,
  onCancel,
}: {
  search: SavedSearch;
  presets: Preset[];
  onCancel: () => void;
}) {
  const patch = usePatchSavedSearch();
  const prefix = "saved-search-" + search.id;
  const [name, setName] = useState(search.name);
  const [presetId, setPresetId] = useState(search.preset_id);
  const [useDefaultWindow, setUseDefaultWindow] = useState(
    search.window_mode === "rolling_12_months",
  );
  const suggested = defaultWindow();
  const [filedFrom, setFiledFrom] = useState(
    search.filters.filed_from ?? suggested.from,
  );
  const [filedThrough, setFiledThrough] = useState(
    search.filters.filed_through ?? suggested.through,
  );
  const [hitBudget, setHitBudget] = useState(
    search.filters.hit_budget === null ? "" : String(search.filters.hit_budget),
  );
  const [pageSize, setPageSize] = useState(String(search.filters.page_size));
  const [validation, setValidation] = useState<string | null>(null);
  const [invalidField, setInvalidField] = useState<string | null>(null);
  const availability = useTribunalAvailability(presetId);
  const [tribunalSelection, setTribunalSelection] = useState({
    presetId: search.preset_id,
    tribunals: search.filters.tribunals ?? ["TJGO"],
  });
  const availableTribunals = availability.data?.tribunals
    .filter((item) => item.enabled)
    .map((item) => item.tribunal) ?? [];
  const selectedTribunals = availability.data
    ? (tribunalSelection.presetId === presetId
        ? tribunalSelection.tribunals
        : availableTribunals
      ).filter((tribunal) => availableTribunals.includes(tribunal))
    : search.filters.tribunals ?? ["TJGO"];

  function failValidation(message: string, field: string) {
    setValidation(message);
    setInvalidField(field);
    document.getElementById(prefix + "-" + field)?.focus();
  }

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedName = name.trim();
    const budget = hitBudget.trim() === "" ? null : Number(hitBudget);
    const parsedPageSize = Number(pageSize);
    if (!normalizedName) {
      failValidation("Informe um nome para a busca salva.", "name");
      return;
    }
    if (availability.data?.selection_enabled && selectedTribunals.length === 0) {
      failValidation("Selecione ao menos um tribunal para esta busca.", "tribunal-selection");
      return;
    }
    if (budget !== null && (!Number.isInteger(budget) || budget < 1)) {
      failValidation("O limite deve ser um inteiro positivo ou ficar em branco.", "hit-budget");
      return;
    }
    if (
      !Number.isInteger(parsedPageSize) ||
      parsedPageSize < MIN_PAGE_SIZE ||
      parsedPageSize > MAX_PAGE_SIZE
    ) {
      failValidation(
        `O tamanho da página deve estar entre ${MIN_PAGE_SIZE} e ${MAX_PAGE_SIZE}.`,
        "page-size",
      );
      return;
    }
    if (!useDefaultWindow && (!filedFrom || !filedThrough || filedFrom > filedThrough)) {
      failValidation(
        "Informe uma janela válida: a data inicial deve ser anterior ou igual à final.",
        "filed-from",
      );
      return;
    }

    setValidation(null);
    setInvalidField(null);
    patch.mutate(
      {
        id: search.id,
        body: {
          name: normalizedName,
          preset_id: presetId,
          window_mode: useDefaultWindow ? "rolling_12_months" : "fixed",
          filters: {
            tribunals: availability.data?.selection_enabled
              ? selectedTribunals
              : search.filters.tribunals ?? ["TJGO"],
            page_size: parsedPageSize,
            hit_budget: budget,
            ...(useDefaultWindow ? {} : { filed_from: filedFrom, filed_through: filedThrough }),
          },
        },
      },
      { onSuccess: onCancel },
    );
  }

  return (
    <form
      className="space-y-4 rounded-md border bg-muted/20 p-4"
      aria-labelledby={prefix + "-title"}
      onSubmit={submit}
    >
      <div>
        <h4 id={prefix + "-title"} className="font-medium">Editar busca salva</h4>
        <p className="text-sm text-muted-foreground">
          Salvar cria uma nova revisão; não inicia uma coleta.
        </p>
      </div>

      <div className="space-y-1.5 sm:max-w-md">
        <Label htmlFor={prefix + "-name"}>Nome da busca</Label>
        <Input
          id={prefix + "-name"}
          value={name}
          maxLength={160}
          onChange={(event) => setName(event.target.value)}
          aria-invalid={invalidField === "name"}
          aria-describedby={validation ? prefix + "-validation" : undefined}
          required
        />
      </div>

      <fieldset className="space-y-3">
        <legend className="mb-2 text-sm font-semibold">Preset temático</legend>
        {presets.map((preset) => (
          <PresetOption
            key={preset.id}
            preset={preset}
            checked={presetId === preset.id}
            groupName={prefix + "-preset"}
            onSelect={() => setPresetId(preset.id)}
          />
        ))}
        {!presets.some((preset) => preset.id === search.preset_id) ? (
          <p className="text-xs text-warning-foreground" role="status">
            O preset atual ({search.preset_id}, versão {search.preset_version}) não está no catálogo carregado.
          </p>
        ) : null}
      </fieldset>

      {availability.data?.selection_enabled ? (
        <TribunalSelector
          items={availability.data.tribunals}
          selected={selectedTribunals}
          onChange={(tribunals) => setTribunalSelection({ presetId, tribunals })}
          label="Tribunais da próxima revisão"
        />
      ) : null}
      {availability.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Não foi possível carregar a allowlist de tribunais. Tente novamente antes de alterar esta revisão.
        </p>
      ) : null}

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
            <Label htmlFor={prefix + "-filed-from"}>Ajuizados a partir de</Label>
            <Input
              id={prefix + "-filed-from"}
              type="date"
              value={filedFrom}
              onChange={(event) => setFiledFrom(event.target.value)}
              disabled={useDefaultWindow}
              required={!useDefaultWindow}
              aria-invalid={invalidField === "filed-from"}
              aria-describedby={validation ? prefix + "-validation" : undefined}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={prefix + "-filed-through"}>Ajuizados até (inclusive)</Label>
            <Input
              id={prefix + "-filed-through"}
              type="date"
              value={filedThrough}
              onChange={(event) => setFiledThrough(event.target.value)}
              disabled={useDefaultWindow}
              required={!useDefaultWindow}
              aria-invalid={invalidField === "filed-through"}
              aria-describedby={validation ? prefix + "-validation" : undefined}
            />
          </div>
        </div>
      </fieldset>

      <FieldGroup className="grid gap-3 sm:grid-cols-2">
        <Field data-invalid={invalidField === "page-size"}>
          <FieldLabel htmlFor={prefix + "-page-size"}>Registros por página</FieldLabel>
          <Input
            id={prefix + "-page-size"}
            type="number"
            inputMode="numeric"
            min={MIN_PAGE_SIZE}
            max={MAX_PAGE_SIZE}
            step={1}
            value={pageSize}
            onChange={(event) => setPageSize(event.target.value)}
            aria-invalid={invalidField === "page-size"}
            aria-describedby={validation ? prefix + "-validation" : prefix + "-page-size-help"}
            required
          />
          <FieldDescription id={prefix + "-page-size-help"}>
            De {MIN_PAGE_SIZE} a {MAX_PAGE_SIZE} por requisição.
          </FieldDescription>
        </Field>
        <Field data-invalid={invalidField === "hit-budget"}>
          <FieldLabel htmlFor={prefix + "-hit-budget"}>Limite cumulativo de registros</FieldLabel>
          <Input
            id={prefix + "-hit-budget"}
            type="number"
            inputMode="numeric"
            min={1}
            step={1}
            value={hitBudget}
            onChange={(event) => setHitBudget(event.target.value)}
            aria-invalid={invalidField === "hit-budget"}
            aria-describedby={validation ? prefix + "-validation" : prefix + "-hit-budget-help"}
          />
          <FieldDescription id={prefix + "-hit-budget-help"}>
            Opcional por tribunal. Deixe em branco para seguir até a página vazia.
          </FieldDescription>
        </Field>
      </FieldGroup>

      {validation ? (
        <p id={prefix + "-validation"} role="alert" className="text-sm font-medium text-destructive">
          {validation}
        </p>
      ) : null}
      {patch.error ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>A busca não foi atualizada</AlertTitle>
          <AlertDescription>{describeError(patch.error)}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={patch.isPending}>
          {patch.isPending ? "Salvando…" : "Salvar alterações"}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel} disabled={patch.isPending}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

function SavedSearchRow({ search, presets }: { search: SavedSearch; presets: Preset[] }) {
  const navigate = useNavigate();
  const patch = usePatchSavedSearch();
  const run = useRunSavedSearch();
  const [editing, setEditing] = useState(false);
  const actionError = patch.error ?? run.error;

  return (
    <li className="space-y-3 py-4" data-testid="saved-search" data-search-id={search.id}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h3 className="font-medium">{search.name}</h3>
          <p className="text-xs text-muted-foreground">
            {search.preset_id} · tribunais {(search.filters.tribunals ?? ["TJGO"]).join(", ")} · preset {search.preset_version} · busca v{search.version}
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
          {search.last_dispatch?.group_id ? (
            <Link
              to={`/job-groups/${search.last_dispatch.group_id}`}
              className="text-xs text-primary underline-offset-4 hover:underline"
            >
              Abrir último grupo agendado
            </Link>
          ) : null}
          {search.last_dispatch?.missed_from ? (
            <p className="text-xs text-warning-foreground">
              Intervalo perdido: {search.last_dispatch.missed_from} a {search.last_dispatch.missed_through}
            </p>
          ) : null}
          {search.availability.reasons.length ? (
            <div className="space-y-1 text-sm text-warning-foreground" role="status">
              {search.availability.reasons.map((reason) => <p key={reason}>{reason}</p>)}
            </div>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          {editing ? null : (
            <>
              <Button
                variant="outline"
                onClick={() => patch.mutate({ id: search.id, body: { enabled: !search.enabled } })}
                disabled={patch.isPending || run.isPending}
              >
                {search.enabled ? "Desativar" : "Ativar"}
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  patch.reset();
                  setEditing(true);
                }}
                disabled={patch.isPending || run.isPending}
              >
                Editar
              </Button>
              <Button
                onClick={() =>
                  run.mutate(search.id, {
                    onSuccess: (created) =>
                      void navigate(
                        `/job-groups/${created.id}`,
                        { state: { created: true } },
                      ),
                  })
                }
                disabled={!search.availability.enabled || run.isPending || patch.isPending}
              >
                {run.isPending ? "Enfileirando…" : "Executar agora"}
              </Button>
            </>
          )}
        </div>
      </div>
      {actionError && !editing ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>A ação não foi concluída</AlertTitle>
          <AlertDescription>{describeError(actionError)}</AlertDescription>
        </Alert>
      ) : null}
      {editing ? (
        <SavedSearchEditForm
          search={search}
          presets={presets}
          onCancel={() => setEditing(false)}
        />
      ) : null}
    </li>
  );
}

function SavedSearchesPanel({ presets }: { presets: Preset[] }) {
  const searches = useSavedSearches();
  return (
    <section className="pt-2" aria-labelledby="saved-searches-title">
      <div className="mb-3">
        <h2 id="saved-searches-title" className="text-lg font-semibold">Buscas salvas</h2>
        <p className="text-sm text-muted-foreground">
          Atualizações automáticas ocorrem às 06h no fuso de São Paulo. Executar agora é uma ação manual.
        </p>
      </div>
      {searches.data ? <StaleNotice query={searches} /> : null}
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
          {searches.data.map((search) => (
            <SavedSearchRow key={search.id} search={search} presets={presets} />
          ))}
        </ul>
      )}
    </section>
  );
}

function PresetOption({
  preset,
  checked,
  groupName,
  onSelect,
}: {
  preset: Preset;
  checked: boolean;
  groupName: string;
  onSelect: () => void;
}) {
  const id = useId();
  const enabled = preset.availability.enabled;
  return (
    <div
      className={cn(
        "rounded-md border px-4 py-3 transition-colors",
        checked && "border-primary bg-primary-soft",
        !enabled && "bg-muted/60",
      )}
      data-preset={preset.id}
      data-enabled={enabled}
    >
      <div className="flex items-start gap-3">
        <input
          id={id}
          type="radio"
          name={groupName}
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
  const createJobGroup = useCreateJobGroup();
  const createSavedSearch = useCreateSavedSearch();
  // Blocks a second submit before React re-renders the disabled button.
  const submitting = useRef(false);

  const suggested = defaultWindow();
  const [useDefaultWindow, setUseDefaultWindow] = useState(true);
  const [filedFrom, setFiledFrom] = useState(suggested.from);
  const [filedThrough, setFiledThrough] = useState(suggested.through);
  const [hitBudget, setHitBudget] = useState("");
  const [pageSize, setPageSize] = useState(String(DEFAULT_PAGE_SIZE));
  const [validation, setValidation] = useState<string | null>(null);
  const [invalidField, setInvalidField] = useState<string | null>(null);
  function failValidation(message: string, field: string) {
    setValidation(message); setInvalidField(field);
    document.getElementById(field)?.focus();
  }
  const [savedSearchName, setSavedSearchName] = useState("");
  const [enableSavedSearch, setEnableSavedSearch] = useState(false);

  const requestedId = searchParams.get("preset");
  const enabledPresets = presets.filter((preset) => preset.availability.enabled);
  const selected =
    enabledPresets.find((preset) => preset.id === requestedId) ?? enabledPresets[0] ?? null;
  const availability = useTribunalAvailability(selected?.id);
  const [tribunalSelection, setTribunalSelection] = useState<{ presetId: string; tribunals: string[] } | null>(null);
  const availableTribunals = availability.data?.tribunals
    .filter((item) => item.enabled)
    .map((item) => item.tribunal) ?? [];
  const groupSelectionEnabled = availability.data?.selection_enabled === true;
  const selectedTribunals = groupSelectionEnabled && selected
    ? (tribunalSelection?.presetId === selected.id
        ? tribunalSelection.tribunals
        : availableTribunals
      ).filter((tribunal) => availableTribunals.includes(tribunal))
    : [];
  const availabilityFailed = availability.isError;
  const sourceBlocked = !environment.source_enabled;

  function selectPreset(id: string) {
    const next = new URLSearchParams(searchParams);
    next.set("preset", id);
    setSearchParams(next, { replace: true });
  }

  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || !selected || sourceBlocked || availabilityFailed || !groupSelectionEnabled) return;
    if (groupSelectionEnabled && selectedTribunals.length === 0) {
      failValidation("Selecione ao menos um tribunal para esta coleta.", "tribunal-selection");
      return;
    }
    const budget = hitBudget.trim() === "" ? null : Number(hitBudget);
    const parsedPageSize = Number(pageSize);
    if (budget !== null && (!Number.isInteger(budget) || budget < 1)) {
      failValidation("O limite deve ser um inteiro positivo ou ficar em branco.", "hit-budget");
      return;
    }
    if (
      !Number.isInteger(parsedPageSize) ||
      parsedPageSize < MIN_PAGE_SIZE ||
      parsedPageSize > MAX_PAGE_SIZE
    ) {
      failValidation(
        `O tamanho da página deve estar entre ${MIN_PAGE_SIZE} e ${MAX_PAGE_SIZE}.`,
        "page-size",
      );
      return;
    }
    if (!useDefaultWindow && (!filedFrom || !filedThrough || filedFrom > filedThrough)) {
      failValidation("Informe uma janela válida: a data inicial deve ser anterior ou igual à final.", "filed-from");
      return;
    }
    setValidation(null); setInvalidField(null);
    submitting.current = true;
    try {
      const criteria = {
          preset_id: selected.id,
          hit_budget: budget,
          page_size: parsedPageSize,
          ...(useDefaultWindow ? {} : { filed_from: filedFrom, filed_through: filedThrough }),
        };
      const group = await createJobGroup.mutateAsync({
        kind: "discovery",
        criteria,
        tribunals: selectedTribunals,
      });
      navigate(`/job-groups/${group.id}`, { state: { created: true } });
    } catch {
      // The mutation error is rendered below; nothing was started on our side.
    } finally {
      submitting.current = false;
    }
  }

  async function saveSearch() {
    if (!selected || sourceBlocked || availabilityFailed || !groupSelectionEnabled) return;
    if (groupSelectionEnabled && selectedTribunals.length === 0) {
      failValidation("Selecione ao menos um tribunal para esta busca.", "tribunal-selection");
      return;
    }
    const budget = hitBudget.trim() === "" ? null : Number(hitBudget);
    const parsedPageSize = Number(pageSize);
    if (!savedSearchName.trim()) {
      failValidation("Informe um nome para a busca salva.", "saved-search-name");
      return;
    }
    if (budget !== null && (!Number.isInteger(budget) || budget < 1)) {
      failValidation("O limite deve ser um inteiro positivo ou ficar em branco.", "hit-budget");
      return;
    }
    if (
      !Number.isInteger(parsedPageSize) ||
      parsedPageSize < MIN_PAGE_SIZE ||
      parsedPageSize > MAX_PAGE_SIZE
    ) {
      failValidation(
        `O tamanho da página deve estar entre ${MIN_PAGE_SIZE} e ${MAX_PAGE_SIZE}.`,
        "page-size",
      );
      return;
    }
    if (!useDefaultWindow && (!filedFrom || !filedThrough || filedFrom > filedThrough)) {
      failValidation("Informe uma janela válida: a data inicial deve ser anterior ou igual à final.", "filed-from");
      return;
    }
    setValidation(null); setInvalidField(null);
    try {
      await createSavedSearch.mutateAsync({
        name: savedSearchName.trim(),
        preset_id: selected.id,
        window_mode: useDefaultWindow ? "rolling_12_months" : "fixed",
        filters: {
          tribunals: selectedTribunals,
          page_size: parsedPageSize,
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

  const pending = createJobGroup.isPending;

  return (
    <form onSubmit={submit} className="grid items-start gap-6 pb-24 xl:pb-0 xl:grid-cols-[minmax(0,1fr)_320px]" aria-describedby="collection-form-help">
      <div className="flex min-w-0 flex-col gap-5">
      <p id="collection-form-help" className="text-sm text-muted-foreground">
        Selecione o tema, o período e o escopo de tribunais. Cada tribunal terá progresso e
        resultado próprios; uma falha não apaga os resultados já confirmados nos demais.
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
      {availabilityFailed ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>Não foi possível carregar a allowlist de tribunais</AlertTitle>
          <AlertDescription>{describeError(availability.error)}</AlertDescription>
        </Alert>
      ) : null}

      <fieldset className="space-y-3">
        <legend className="mb-2 text-sm font-semibold">Preset temático</legend>
        {presets.map((preset) => (
          <PresetOption
            key={preset.id}
            preset={preset}
            checked={selected?.id === preset.id}
            groupName="radar-presets"
            onSelect={() => selectPreset(preset.id)}
          />
        ))}
      </fieldset>

      {groupSelectionEnabled && availability.data ? (
        <div id="tribunal-selection">
          <TribunalSelector
            items={availability.data.tribunals}
            selected={selectedTribunals}
            onChange={(tribunals) => selected && setTribunalSelection({ presetId: selected.id, tribunals })}
          />
        </div>
      ) : availability.isPending ? (
        <p className="text-sm text-muted-foreground" role="status">Carregando os 33 tribunais da allowlist…</p>
      ) : null}

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
              name="filed-from" aria-invalid={invalidField === "filed-from"} aria-describedby={invalidField === "filed-from" ? "collection-validation" : undefined}
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
              name="filed-through" aria-invalid={invalidField === "filed-through"} aria-describedby={invalidField === "filed-through" ? "collection-validation" : undefined}
              type="date"
              value={filedThrough}
              onChange={(event) => setFiledThrough(event.target.value)}
              disabled={useDefaultWindow}
              required={!useDefaultWindow}
            />
          </div>
        </div>
      </fieldset>

      <FieldGroup className="grid gap-3 sm:grid-cols-2">
        <Field data-invalid={invalidField === "page-size"}>
          <FieldLabel htmlFor="page-size">Registros por página</FieldLabel>
          <Input
            id="page-size"
            name="page-size"
            aria-invalid={invalidField === "page-size"}
            type="number"
            inputMode="numeric"
            min={MIN_PAGE_SIZE}
            max={MAX_PAGE_SIZE}
            step={1}
            value={pageSize}
            onChange={(event) => setPageSize(event.target.value)}
            aria-describedby="page-size-help collection-validation"
            required
          />
          <FieldDescription id="page-size-help">
            De {MIN_PAGE_SIZE} a {MAX_PAGE_SIZE} registros por requisição.
          </FieldDescription>
        </Field>
        <Field data-invalid={invalidField === "hit-budget"}>
          <FieldLabel htmlFor="hit-budget">Limite cumulativo por tribunal</FieldLabel>
          <Input
            id="hit-budget"
            name="hit-budget"
            aria-invalid={invalidField === "hit-budget"}
            type="number"
            inputMode="numeric"
            min={1}
            step={1}
            value={hitBudget}
            onChange={(event) => setHitBudget(event.target.value)}
            aria-describedby="hit-budget-help collection-validation"
          />
          <FieldDescription id="hit-budget-help">
            Opcional. Deixe em branco para seguir até a página vazia; ao atingir o limite a coleta
            fica parcial e pode ser continuada.
          </FieldDescription>
        </Field>
      </FieldGroup>

      {validation ? (
        <p id="collection-validation" role="alert" className="text-sm font-medium text-destructive">
          {validation}
        </p>
      ) : null}
      {createJobGroup.isError ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>A coleta não foi iniciada</AlertTitle>
          <AlertDescription>{describeError(createJobGroup.error)}</AlertDescription>
        </Alert>
      ) : null}

      <details className="space-y-3 rounded-md border p-4">
        <summary className="font-medium">Salvar busca</summary>
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
              name="saved-search-name" aria-invalid={invalidField === "saved-search-name"} aria-describedby={invalidField === "saved-search-name" ? "collection-validation" : undefined}
            value={savedSearchName}
            maxLength={160}
            onChange={(event) => setSavedSearchName(event.target.value)}
            placeholder="Ex.: Crédito rural em mais de um tribunal"
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
          <p role="status" className="text-sm text-success-foreground">Busca salva.</p>
        ) : null}
        <Button type="button" variant="outline" onClick={() => void saveSearch()} disabled={createSavedSearch.isPending || !selected || sourceBlocked || availabilityFailed || !groupSelectionEnabled || selectedTribunals.length === 0}>
          {createSavedSearch.isPending ? "Salvando…" : "Salvar busca"}
        </Button>
      </details>
      </div>
      <aside className="flex flex-col gap-5 xl:sticky xl:top-20" aria-label="Resumo da coleta e coletas recentes">
        <Card>
          <CardHeader><CardTitle>Resumo da coleta</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-4">
            <dl className="flex flex-col gap-3 text-sm">
              <div><dt className="text-xs text-muted-foreground">Tema</dt><dd className="font-medium">{selected?.name ?? "Nenhum tema disponível"}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Escopo da consulta</dt><dd>{selectedTribunals.length === 33 ? "Todos os 33 tribunais (nacional)" : `${selectedTribunals.length} de 33 tribunais selecionados`}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Período de ajuizamento</dt><dd>{useDefaultWindow ? "Últimos 12 meses, calculados ao iniciar" : `${filedFrom} a ${filedThrough}`}</dd></div>
              <div><dt className="text-xs text-muted-foreground">Limite desta execução</dt><dd>{hitBudget} registros</dd></div>
            </dl>

            <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-card p-3 shadow-sm xl:static xl:border-0 xl:bg-transparent xl:p-0 xl:shadow-none">
              <Button type="submit" size="lg" className="w-full xl:w-auto" disabled={pending || !selected || sourceBlocked || availabilityFailed || !groupSelectionEnabled || selectedTribunals.length === 0}>
                {pending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <Play aria-hidden="true" />
                )}
                {pending ? "Enviando…" : "Iniciar coleta"}
              </Button>
            </div>
          </CardContent>
        </Card>
        <Card><CardHeader><CardTitle>Coletas recentes</CardTitle></CardHeader><CardContent><RecentJobs /></CardContent></Card>
      </aside>
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
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "saved" ? "saved" : "new";
  return <>
    <PageHeader title="Radar" description="Descubra processos em um, vários ou todos os 33 tribunais da allowlist DataJud." />
    <Tabs value={tab} onValueChange={value => { const next = new URLSearchParams(params); next.set("tab", value); setParams(next, { preventScrollReset: true }); }}>
      <TabsList aria-label="Radar"><TabsTrigger value="new">Nova coleta</TabsTrigger><TabsTrigger value="saved">Buscas salvas</TabsTrigger></TabsList>
      <TabsContent value="new" forceMount>
        {presets.isPending ? <LoadingState label="Carregando presets…" variant="detail" /> : presets.data ? <><StaleNotice query={presets} /><CollectionForm presets={presets.data.items} /></> : <ErrorState title="Não foi possível carregar os presets" error={presets.error} onRetry={() => void presets.refetch()} retrying={presets.isFetching} />}
      </TabsContent>
      <TabsContent value="saved"><Card><CardContent><SavedSearchesPanel presets={presets.data?.items ?? []} /></CardContent></Card></TabsContent>
    </Tabs>
  </>;
}
