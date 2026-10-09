import { Search } from "lucide-react";
import { useState, type SubmitEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { useProcessList } from "@/api/queries";
import type {
  ProcessListQuery,
  ProcessSummary,
  RuralLink,
  TriageDecision,
} from "@/api/types";
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
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
const UUID_PATTERN = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const DECISIONS = ["pending", "relevant", "discarded"] as const satisfies readonly TriageDecision[];
const RURAL_LINKS = ["unconfirmed", "confirmed"] as const satisfies readonly RuralLink[];

const FILTER_FIELDS = [
  { key: "process_number", label: "Número CNJ", placeholder: "0000000-00.0000.0.00.0000" },
  { key: "subject", label: "Assunto", placeholder: "Nome ou código" },
  { key: "class", label: "Classe", placeholder: "Nome ou código" },
  { key: "court_unit", label: "Órgão julgador", placeholder: "Nome ou código" },
  { key: "preset_id", label: "Preset", placeholder: "ex.: rural.credito_contratos" },
  { key: "collection_id", label: "Coleta (ID)", placeholder: "UUID da coleta" },
] as const;

type FilterKey = (typeof FILTER_FIELDS)[number]["key"];
type FilterValues = Record<FilterKey, string> & {
  decision: TriageDecision | "";
  rural_link: RuralLink | "";
};

function readFilters(params: URLSearchParams): FilterValues {
  const textFilters = Object.fromEntries(
    FILTER_FIELDS.map((field) => [field.key, readText(params, field.key) ?? ""]),
  ) as Record<FilterKey, string>;
  return {
    ...textFilters,
    decision: readEnum(params, "decision", DECISIONS) ?? "",
    rural_link: readEnum(params, "rural_link", RURAL_LINKS) ?? "",
  };
}

function FilterForm({
  initial,
  onApply,
  onClear,
}: {
  initial: FilterValues;
  onApply: (values: FilterValues) => void;
  onClear: () => void;
}) {
  const [values, setValues] = useState(initial);
  const hasFilters = Object.values(initial).some(Boolean);

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    onApply(values);
  }

  return (
    <form role="search" aria-label="Filtrar processos" onSubmit={submit} className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {FILTER_FIELDS.map((field) => (
          <div key={field.key} className="space-y-1.5">
            <Label htmlFor={`filter-${field.key}`}>{field.label}</Label>
            <Input
              id={`filter-${field.key}`}
              name={field.key}
              value={values[field.key]}
              placeholder={field.placeholder}
              pattern={field.key === "collection_id" ? UUID_PATTERN : undefined}
              title={field.key === "collection_id" ? "Informe um UUID válido." : undefined}
              maxLength={field.key === "process_number" ? 30 : 160}
              onChange={(event) =>
                setValues((current) => ({ ...current, [field.key]: event.target.value }))
              }
            />
          </div>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="filter-decision">Decisão da triagem</Label>
          <NativeSelect
            id="filter-decision"
            value={values.decision}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                decision: event.target.value as FilterValues["decision"],
              }))
            }
          >
            <option value="">Todas as decisões</option>
            <option value="pending">Pendente</option>
            <option value="relevant">Relevante</option>
            <option value="discarded">Descartado</option>
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="filter-rural-link">Vínculo rural</Label>
          <NativeSelect
            id="filter-rural-link"
            value={values.rural_link}
            onChange={(event) =>
              setValues((current) => ({
                ...current,
                rural_link: event.target.value as FilterValues["rural_link"],
              }))
            }
          >
            <option value="">Todos os vínculos</option>
            <option value="unconfirmed">Não confirmado</option>
            <option value="confirmed">Confirmado manualmente</option>
          </NativeSelect>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="submit">
          <Search aria-hidden="true" />
          Pesquisar
        </Button>
        <Button type="button" variant="ghost" disabled={!hasFilters} onClick={onClear}>
          Limpar filtros
        </Button>
      </div>
    </form>
  );
}

function ProcessesTable({ processes }: { processes: ProcessSummary[] }) {
  return (
    <Table>
      <TableCaption className="sr-only">Processos locais ordenados por número CNJ</TableCaption>
      <TableHeader>
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
              <TableCell className="whitespace-nowrap">
                <Link
                  to={`/processes/${process.id}`}
                  className="font-medium text-primary tabular-nums underline-offset-4 hover:underline"
                >
                  {formatCnj(process.numero_cnj)}
                </Link>
              </TableCell>
              <TableCell className="whitespace-nowrap text-sm">
                <div>{triageDecisionLabel(process.triage.decision)}</div>
                <div className="text-muted-foreground">
                  {ruralLinkLabel(process.triage.rural_link)}
                </div>
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatCount(process.representation_count)}
              </TableCell>
              <TableCell className="whitespace-nowrap">
                {formatDateTime(process.latest_observed_at)}
              </TableCell>
              <TableCell>
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

function triageDecisionLabel(value: TriageDecision): string {
  return { pending: "Pendente", relevant: "Relevante", discarded: "Descartado" }[value];
}

function ruralLinkLabel(value: RuralLink): string {
  return { unconfirmed: "Vínculo não confirmado", confirmed: "Vínculo confirmado" }[value];
}

export function ProcessesPage() {
  usePageTitle("Processos");
  const [searchParams, setSearchParams] = useSearchParams();
  const page = readPage(searchParams);
  const filters = readFilters(searchParams);
  const query: ProcessListQuery = { page, page_size: PAGE_SIZE };
  for (const field of FILTER_FIELDS) {
    const value = filters[field.key];
    if (value) query[field.key] = value;
  }
  if (filters.decision) query.decision = filters.decision;
  if (filters.rural_link) query.rural_link = filters.rural_link;
  const processes = useProcessList(query);
  const filtered = Object.values(filters).some(Boolean);

  return (
    <>
      <PageHeader
        title="Processos"
        description="Base local agrupada por número CNJ. Os dados mostrados vêm de coletas já persistidas."
      />
      <Card>
        <CardContent className="space-y-6">
          <FilterForm
            key={searchParams.toString()}
            initial={filters}
            onApply={(values) => setSearchParams(withFilters(searchParams, values))}
            onClear={() => setSearchParams(new URLSearchParams())}
          />
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
