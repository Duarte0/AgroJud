import { Download, Search, SlidersHorizontal } from "lucide-react";
import { useState, type SubmitEvent } from "react";
import { Link, useLocation, useSearchParams } from "react-router";

import { useProcessFilterOptions, useProcessList } from "@/api/queries";
import { describeError, downloadProcessCsv } from "@/api/client";
import type {
  ProcessFilterOption,
  ProcessListQuery,
  ProcessSummary,
  RuralLink,
  TriageDecision,
} from "@/api/types";
import { AppliedFilters } from "@/components/applied-filters";
import { FilterAutocomplete } from "@/components/filter-autocomplete";
import { ProcessState } from "@/components/process-state";
import { PageHeader } from "@/components/page-header";
import { usePageTitle } from "@/hooks/use-page-title";
import { Pagination } from "@/components/pagination";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleNotice,
} from "@/components/query-feedback";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  captureOutcomeLabel,
  formatCnj,
  formatCount,
  formatDateTime,
  jobStatusLabel,
} from "@/lib/format";
import { readEnum, readPage, readText, withFilters, withPage } from "@/lib/search-params";

const PAGE_SIZE = 25;
const DECISIONS = ["pending", "relevant", "discarded"] as const satisfies readonly TriageDecision[];
const RURAL_LINKS = ["unconfirmed", "confirmed"] as const satisfies readonly RuralLink[];

const FILTER_FIELDS = [
  { key: "process_number", label: "Número CNJ", placeholder: "0000000-00.0000.0.00.0000" },
  { key: "subject", label: "Assunto", placeholder: "Nome ou código" },
  { key: "subject_code", label: "Código exato do tema", placeholder: "ex.: 4968" },
  { key: "subject_name_exact", label: "Nome exato do tema", placeholder: "Nome cadastrado" },
  { key: "class", label: "Classe", placeholder: "Nome ou código" },
  { key: "court_unit", label: "Órgão julgador", placeholder: "Nome ou código" },
  { key: "preset_id", label: "Preset", placeholder: "ex.: rural.credito_contratos" },
  { key: "collection_id", label: "Coleta", placeholder: "Digite a data, o tema ou o status" },
  { key: "signal_category", label: "Categoria de sinal vigente", placeholder: "ex.: penhora" },
] as const;

type FilterKey = (typeof FILTER_FIELDS)[number]["key"];
type FilterValues = Record<FilterKey, string> & {
  decision: TriageDecision | "";
  rural_link: RuralLink | "";
  followed: "" | "true" | "false";
  pending_news: "" | "true" | "false";
};

function readBooleanFilter(params: URLSearchParams, key: string): FilterValues["followed"] {
  const value = params.get(key);
  return value === "true" || value === "false" ? value : "";
}

function readFilters(params: URLSearchParams): FilterValues {
  const textFilters = Object.fromEntries(
    FILTER_FIELDS.map((field) => [field.key, readText(params, field.key) ?? ""]),
  ) as Record<FilterKey, string>;
  return {
    ...textFilters,
    decision: readEnum(params, "decision", DECISIONS) ?? "",
    rural_link: readEnum(params, "rural_link", RURAL_LINKS) ?? "",
    followed: readBooleanFilter(params, "followed"),
    pending_news: readBooleanFilter(params, "pending_news"),
  };
}

function FilterForm({ initial, collectionLabel, onApply, onClear }: {
  initial: FilterValues;
  collectionLabel: string | undefined;
  onApply: (values: FilterValues) => void;
  onClear: () => void;
}) {
  const [values, setValues] = useState(initial);
  const [drafts, setDrafts] = useState<Record<FilterKey, string>>(() =>
    Object.fromEntries(FILTER_FIELDS.map(field => [field.key, field.key === "collection_id" ? "" : initial[field.key]])) as Record<FilterKey, string>,
  );
  const [collectionError, setCollectionError] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const advancedCount = Object.entries(initial).filter(([key, value]) => !["process_number", "subject", "decision"].includes(key) && value).length;
  function textField(field: (typeof FILTER_FIELDS)[number]) {
    const value = field.key === "collection_id"
      ? drafts.collection_id || (values.collection_id ? collectionLabel ?? "Carregando coleta…" : "")
      : drafts[field.key];
    return <div key={field.key} className="flex min-w-0 flex-col gap-1.5">
      <Label htmlFor={`filter-${field.key}`}>{field.label}</Label>
      <FilterAutocomplete
        id={`filter-${field.key}`}
        name={field.key}
        value={value}
        selectedValue={field.key === "collection_id" ? values.collection_id : undefined}
        placeholder={field.placeholder}
        maxLength={field.key === "process_number" ? 30 : field.key === "subject_code" ? 80 : 160}
        onChange={next => {
          setDrafts(current => ({ ...current, [field.key]: next }));
          setValues(current => ({ ...current, [field.key]: field.key === "collection_id" ? "" : next }));
          if (field.key === "collection_id") setCollectionError(false);
        }}
        onSelect={(option: ProcessFilterOption) => {
          setDrafts(current => ({ ...current, [field.key]: option.label }));
          setValues(current => ({ ...current, [field.key]: option.value }));
          if (field.key === "collection_id") setCollectionError(false);
        }}
      />
      {field.key === "collection_id" ? (
        <>
          <p className="text-xs text-muted-foreground">Escolha uma coleta sugerida para aplicar o filtro.</p>
          {collectionError ? <p className="text-xs text-destructive" role="alert">Selecione uma coleta da lista para usar este filtro.</p> : null}
        </>
      ) : null}
    </div>;
  }
  function selectField(key: "decision" | "followed" | "pending_news" | "rural_link", label: string, options: [string, string][]) {
    return <div className="flex flex-col gap-1.5" key={key}><Label htmlFor={`filter-${key}`}>{label}</Label>
      <NativeSelect id={`filter-${key}`} name={key} value={values[key]} onChange={event => setValues(current => ({...current, [key]: event.target.value}))}>
        {options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
      </NativeSelect></div>;
  }
  return <form role="search" aria-label="Filtrar processos" onSubmit={(event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (drafts.collection_id.trim() && !values.collection_id) {
      setCollectionError(true);
      return;
    }
    setCollectionError(false);
    onApply(values);
  }} className="flex flex-col gap-4">
    <div className="grid gap-3 md:grid-cols-3">
      {FILTER_FIELDS.filter(field => ["process_number", "subject"].includes(field.key)).map(textField)}
      {selectField("decision", "Decisão da triagem", [["", "Todas as decisões"], ["pending", "Pendente"], ["relevant", "Relevante"], ["discarded", "Descartado"]])}
    </div>
    <div className="flex flex-wrap gap-2">
      <Button type="submit"><Search aria-hidden="true" />Pesquisar</Button>
      <Button type="button" variant="outline" aria-expanded={expanded} aria-controls="advanced-filters" onClick={() => setExpanded(!expanded)}><SlidersHorizontal aria-hidden="true" />Mais filtros{advancedCount ? ` (${advancedCount})` : ""}</Button>
      <Button type="button" variant="ghost" disabled={!Object.values(initial).some(Boolean)} onClick={onClear}>Limpar filtros</Button>
    </div>
    <div id="advanced-filters" hidden={!expanded} className="grid gap-3 rounded-md bg-muted/50 p-4 sm:grid-cols-2 lg:grid-cols-3">
      {FILTER_FIELDS.filter(field => !["process_number", "subject"].includes(field.key)).map(textField)}
      {selectField("followed", "Acompanhamento ativo", [["", "Todos"], ["true", "Acompanhados"], ["false", "Sem acompanhamento ativo"]])}
      {selectField("pending_news", "Novidades pendentes", [["", "Todos"], ["true", "Com novidades pendentes"], ["false", "Sem novidades pendentes"]])}
      {selectField("rural_link", "Vínculo rural", [["", "Todos os vínculos"], ["unconfirmed", "Não confirmado"], ["confirmed", "Confirmado manualmente"]])}
    </div>
  </form>;
}

function ProcessesTable({ processes }: { processes: ProcessSummary[] }) {
  const location = useLocation();
  return (
    <Table className="responsive-records">
      <TableCaption className="sr-only">Processos locais ordenados por número CNJ</TableCaption>
      <TableHeader className="sr-only md:not-sr-only">
        <TableRow>
          <TableHead>Número CNJ</TableHead>
          <TableHead>Triagem</TableHead>
          <TableHead className="text-right">Capas</TableHead>
          <TableHead>Última observação local</TableHead>
          <TableHead>Última coleta</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {processes.map((process) => {
          const latest = process.latest_collection;
          return (
            <TableRow key={process.id}>
              <TableCell data-label="Número CNJ" className="whitespace-nowrap">
                <Link
                  to={`/processes/${process.id}`}
                  state={{ from: location.pathname + location.search }}
                  className="cnj font-medium text-primary underline-offset-4 hover:underline"
                >
                  {formatCnj(process.numero_cnj)}
                </Link>
              </TableCell>
              <TableCell data-label="Triagem"><ProcessState decision={process.triage.decision} ruralLink={process.triage.rural_link} /></TableCell>
              <TableCell data-label="Capas" className="text-right tabular-nums">
                {formatCount(process.representation_count)}
              </TableCell>
              <TableCell data-label="Última observação local" className="whitespace-nowrap">
                {formatDateTime(process.latest_observed_at)}
              </TableCell>
              <TableCell data-label="Última coleta">
                {latest ? (
                  <span className="text-sm">
                    {captureOutcomeLabel(latest.capture_outcome)} em{" "}
                    {formatDateTime(latest.included_at)}
                    {latest.job_id ? (
                      <>
                        {" · "}
                        <Link
                          to={`/jobs/${latest.job_id}`}
                          className="text-primary underline-offset-4 hover:underline"
                        >
                          ver coleta
                          {latest.job_status ? ` (${jobStatusLabel(latest.job_status).toLowerCase()})` : ""}
                        </Link>
                      </>
                    ) : null}
                    {latest.environment === "demo" ? " · sintético" : ""}
                  </span>
                ) : (
                  "Não informado"
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export function ProcessesPage() {
  usePageTitle("Processos");
  const [searchParams, setSearchParams] = useSearchParams();
  const [exportPending, setExportPending] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const page = readPage(searchParams);
  const filters = readFilters(searchParams);
  const collectionOptions = useProcessFilterOptions(
    "collection_id",
    "",
    filters.collection_id,
    Boolean(filters.collection_id),
  );
  const selectedCollection = collectionOptions.data?.items.find(
    option => option.value === filters.collection_id,
  );
  const collectionLabel = selectedCollection?.label ?? (filters.collection_id
    ? collectionOptions.isPending ? "Carregando coleta…" : "Coleta não localizada"
    : undefined);
  const query: ProcessListQuery = { page, page_size: PAGE_SIZE };
  for (const field of FILTER_FIELDS) {
    const value = filters[field.key];
    if (value) query[field.key] = value;
  }
  if (filters.decision) query.decision = filters.decision;
  if (filters.rural_link) query.rural_link = filters.rural_link;
  if (filters.followed) query.followed = filters.followed === "true";
  if (filters.pending_news) query.pending_news = filters.pending_news === "true";
  const processes = useProcessList(query);
  const exportFilters = { ...query };
  delete exportFilters.page;
  delete exportFilters.page_size;
  const filtered = Object.values(filters).some(Boolean);
  const overviewParams = new URLSearchParams(searchParams);
  overviewParams.delete("page");
  const overviewHref = overviewParams.size ? `/?${overviewParams.toString()}` : "/";

  async function exportCsv() {
    setExportPending(true);
    setExportError(null);
    try {
      await downloadProcessCsv(exportFilters);
    } catch (error) {
      setExportError(describeError(error));
    } finally {
      setExportPending(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Processos"
        description="Base local agrupada por número CNJ. Os dados mostrados vêm de coletas já persistidas."

      />
      <Card>
        <CardContent className="space-y-6">
          {exportError ? (
            <Alert variant="destructive" role="alert">
              <AlertTitle>Não foi possível exportar os processos</AlertTitle>
              <AlertDescription>{exportError}</AlertDescription>
            </Alert>
          ) : null}
          <FilterForm
            key={searchParams.toString()}
            initial={filters}
            collectionLabel={collectionLabel}
            onApply={(values) => setSearchParams(withFilters(searchParams, values))}
            onClear={() => setSearchParams(new URLSearchParams())}
          />
          <AppliedFilters values={filters} labels={{ collection_id: collectionLabel ?? "Coleta não localizada" }} onRemove={key => setSearchParams(withFilters(searchParams, { [key]: "" }))} />
          </CardContent>
      </Card>
      <Card className="mt-5">
        <CardContent>
          <div className="data-toolbar">
            <p className="font-medium">{processes.data ? `${formatCount(processes.data.total)} processos` : "Resultados"}<span className="block text-xs font-normal text-muted-foreground">CSV inclui todo o recorte aplicado</span></p>
            <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => void exportCsv()}
              disabled={exportPending}
              aria-busy={exportPending}
            >
              <Download aria-hidden="true" />
              {exportPending ? "Exportando…" : "Exportar CSV"}
            </Button>
            <Link
              to={overviewHref}
              className="inline-flex min-h-9 items-center rounded-md border border-input px-3 text-sm font-medium text-primary-dark underline-offset-4 hover:bg-primary-soft hover:underline"
            >
              Ver indicadores deste recorte
            </Link></div>
          </div>
          {processes.isPending ? (
            <LoadingState label="Carregando processos…" />
          ) : !processes.data ? (
            <ErrorState
              title="Não foi possível carregar os processos"
              error={processes.error}
              onRetry={() => void processes.refetch()}
              retrying={processes.isFetching}
            />
          ) : (
            <div className="space-y-4" aria-busy={processes.isPlaceholderData}>
              <StaleNotice query={processes} />
              {processes.data.items.length === 0 ? (
                <EmptyState
                  title={
                    processes.data.total > 0
                      ? "Esta página não contém processos"
                      : filtered
                        ? "Nenhum processo corresponde aos filtros"
                        : "Nenhum processo na base local"
                  }
                >
                  {filtered
                    ? "A consulta local foi concluída sem resultados. Ajuste ou limpe os filtros."
                    : "Inicie uma coleta pelo radar para popular a base local."}
                </EmptyState>
              ) : (
                <ProcessesTable processes={processes.data.items} />
              )}
              <Pagination
                label="Paginação dos processos"
                page={page}
                pageSize={PAGE_SIZE}
                total={processes.data.total}
                disabled={processes.isPlaceholderData}
                onPageChange={(next) => setSearchParams(withPage(searchParams, next))}
              />
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}
