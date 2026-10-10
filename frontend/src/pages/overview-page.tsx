import { Link, useSearchParams } from "react-router";

import { useOverview } from "@/api/queries";
import type { Overview, OverviewQuery } from "@/api/types";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState, LoadingState, StaleNotice } from "@/components/query-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { usePageTitle } from "@/hooks/use-page-title";
import { formatCount, formatDateTime, jobStatusLabel } from "@/lib/format";

type OverviewFilters = Overview["filters"];
type Metric = Overview["processes"];

const UNIT_LABELS: Record<Metric["unit"], string> = {
  processes: "processos",
  representations: "representações",
  occurrences: "novidades",
  jobs: "jobs",
};

const SINGULAR_UNIT_LABELS: Record<Metric["unit"], string> = {
  processes: "processo",
  representations: "representação",
  occurrences: "novidade",
  jobs: "job",
};

const DECISION_LABELS = {
  pending: "pendente",
  relevant: "relevante",
  discarded: "descartado",
} as const;

const RURAL_LINK_LABELS = {
  unconfirmed: "não confirmado",
  confirmed: "confirmado manualmente",
} as const;

function queryFromSearchParams(params: URLSearchParams): OverviewQuery {
  const query: OverviewQuery = {};
  const text = (key: string) => params.get(key)?.trim() || undefined;
  const boolean = (key: string): boolean | undefined => {
    const value = params.get(key);
    return value === "true" ? true : value === "false" ? false : undefined;
  };

  const processNumber = text("process_number");
  const subject = text("subject");
  const subjectCode = text("subject_code");
  const subjectName = text("subject_name_exact");
  const className = text("class");
  const courtUnit = text("court_unit");
  const collectionId = text("collection_id");
  const presetId = text("preset_id");
  const signalCategory = text("signal_category");
  const decision = params.get("decision");
  const ruralLink = params.get("rural_link");

  if (processNumber) query.process_number = processNumber;
  if (subject) query.subject = subject;
  if (subjectCode) query.subject_code = subjectCode;
  if (subjectName) query.subject_name_exact = subjectName;
  if (className) query["class"] = className;
  if (courtUnit) query.court_unit = courtUnit;
  if (collectionId) query.collection_id = collectionId;
  if (presetId) query.preset_id = presetId;
  if (signalCategory) query.signal_category = signalCategory;
  if (decision === "pending" || decision === "relevant" || decision === "discarded") {
    query.decision = decision;
  }
  if (ruralLink === "unconfirmed" || ruralLink === "confirmed") query.rural_link = ruralLink;
  const followed = boolean("followed");
  const pendingNews = boolean("pending_news");
  if (followed !== undefined) query.followed = followed;
  if (pendingNews !== undefined) query.pending_news = pendingNews;
  return query;
}

function filterParams(filters: OverviewFilters): URLSearchParams {
  const params = new URLSearchParams();
  const values: Record<string, string | boolean | null | undefined> = {
    process_number: filters.process_number,
    subject: filters.subject,
    subject_code: filters.subject_code,
    subject_name_exact: filters.subject_name_exact,
    class: filters["class"],
    court_unit: filters.court_unit,
    collection_id: filters.collection_id,
    preset_id: filters.preset_id,
    decision: filters.decision,
    rural_link: filters.rural_link,
    followed: filters.followed,
    pending_news: filters.pending_news,
    signal_category: filters.signal_category,
  };
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  return params;
}

function processListHref(
  filters: OverviewFilters,
  overrides: Record<string, string | undefined> = {},
): string {
  const params = filterParams(filters);
  for (const [key, value] of Object.entries(overrides)) {
    if (value) params.set(key, value);
    else params.delete(key);
  }
  const query = params.toString();
  return `/processes${query ? `?${query}` : ""}`;
}

function newsListHref(filters: OverviewFilters): string {
  const params = filterParams(filters);
  params.set("status", "pending");
  const query = params.toString();
  return `/news?${query}`;
}

function metricLabel(metric: Metric): string {
  const unit = metric.value === 1 ? SINGULAR_UNIT_LABELS[metric.unit] : UNIT_LABELS[metric.unit];
  return `${formatCount(metric.value)} ${unit}`;
}

function decisionLabel(value: NonNullable<OverviewFilters["decision"]>): string {
  return DECISION_LABELS[value];
}

function ruralLinkLabel(value: NonNullable<OverviewFilters["rural_link"]>): string {
  return RURAL_LINK_LABELS[value];
}

function resolvedFilterLabels(filters: OverviewFilters): Array<[string, string]> {
  const labels: Array<[string, string | null | undefined]> = [
    ["Número CNJ", filters.process_number],
    ["Assunto", filters.subject],
    ["Código exato do tema", filters.subject_code],
    ["Nome exato do tema", filters.subject_name_exact],
    ["Classe", filters["class"]],
    ["Órgão julgador", filters.court_unit],
    ["Coleta", filters.collection_id],
    ["Preset", filters.preset_id],
    ["Triagem", filters.decision ? decisionLabel(filters.decision) : null],
    ["Vínculo rural", filters.rural_link ? ruralLinkLabel(filters.rural_link) : null],
    [
      "Acompanhamento ativo",
      filters.followed === null || filters.followed === undefined
        ? null
        : filters.followed
          ? "sim"
          : "não",
    ],
    [
      "Novidades pendentes",
      filters.pending_news === null || filters.pending_news === undefined
        ? null
        : filters.pending_news
          ? "sim"
          : "não",
    ],
    ["Sinal vigente", filters.signal_category],
  ];
  return labels.filter((entry): entry is [string, string] => Boolean(entry[1]));
}

function MetricCard({
  title,
  metric,
  href,
  detail,
}: {
  title: string;
  metric: Metric;
  href: string;
  detail: string;
}) {
  return (
    <Link
      to={href}
      aria-label={`${title}: ${metricLabel(metric)}. ${detail}`}
      className="block h-full rounded-xl focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <Card className="h-full gap-3 transition-colors hover:border-primary/60">
        <CardHeader className="pb-0">
          <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-2xl font-semibold tabular-nums">{metricLabel(metric)}</p>
          <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
        </CardContent>
      </Card>
    </Link>
  );
}

function CurrentSignals({ overview }: { overview: Overview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sinais vigentes por categoria</CardTitle>
        <CardDescription>
          Processos distintos com regra publicada na versão atual. Histórico não aumenta estas contagens.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {overview.current_signals.length === 0 ? (
          <EmptyState title="Nenhum sinal vigente nesta amostra" />
        ) : (
          <Table>
            <TableCaption className="sr-only">Processos com sinais locais vigentes por categoria</TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead>Categoria</TableHead>
                <TableHead className="text-right">Processos</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.current_signals.map((item) => (
                <TableRow key={item.category}>
                  <TableCell>
                    <Link
                      to={processListHref(overview.filters, { signal_category: item.category })}
                      className="font-medium text-primary underline-offset-4 hover:underline"
                    >
                      {item.category}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{metricLabel(item.processes)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function Themes({ overview }: { overview: Overview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Temas da base</CardTitle>
        <CardDescription>
          Um processo pode ter vários temas; a soma desta tabela pode ser maior que o total de processos.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {overview.themes.length === 0 ? (
          <EmptyState title="Nenhum tema informado nas representações locais" />
        ) : (
          <Table>
            <TableCaption className="sr-only">Processos distintos por tema atual das representações</TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead>Tema</TableHead>
                <TableHead className="text-right">Processos</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.themes.map((theme) => {
                const name = theme.subject_name ?? "Tema sem nome";
                const href = processListHref(overview.filters, {
                  subject_code: theme.subject_code ?? undefined,
                  subject_name_exact: theme.subject_name ?? undefined,
                });
                return (
                  <TableRow key={`${theme.subject_code ?? ""}:${theme.subject_name ?? ""}`}>
                    <TableCell>
                      <Link to={href} className="font-medium text-primary underline-offset-4 hover:underline">
                        {name}
                      </Link>
                      {theme.subject_code ? (
                        <div className="text-xs text-muted-foreground">Código {theme.subject_code}</div>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{metricLabel(theme.processes)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function LatestCollections({ overview }: { overview: Overview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Últimas coletas</CardTitle>
        <CardDescription>
          Execuções recentes da base toda, independentes do recorte de processos acima.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {overview.latest_collections.length === 0 ? (
          <EmptyState title="Nenhuma coleta local registrada" />
        ) : (
          <Table>
            <TableCaption className="sr-only">Últimos jobs de coleta registrados localmente</TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead>Execução</TableHead>
                <TableHead>Fonte</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Iniciada localmente</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overview.latest_collections.map((collection) => (
                <TableRow key={collection.job_id}>
                  <TableCell>
                    <Link
                      to={`/jobs/${collection.job_id}`}
                      className="font-medium text-primary underline-offset-4 hover:underline"
                    >
                      Ver job {collection.job_id.slice(0, 8)}
                    </Link>
                  </TableCell>
                  <TableCell>{collection.environment === "demo" ? "Sintética" : "DataJud"}</TableCell>
                  <TableCell>{jobStatusLabel(collection.status)}</TableCell>
                  <TableCell>{formatDateTime(collection.created_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

export function OverviewPage() {
  usePageTitle("Visão geral");
  const [searchParams] = useSearchParams();
  const query = queryFromSearchParams(searchParams);
  const overview = useOverview(query);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Visão geral"
        description="Indicadores descritivos da amostra persistida localmente; não representam o universo de processos do TJGO."
      />
      {overview.isPending ? (
        <LoadingState label="Carregando indicadores locais…" />
      ) : !overview.data ? (
        <ErrorState
          title="Não foi possível carregar os indicadores"
          error={overview.error}
          onRetry={() => void overview.refetch()}
          retrying={overview.isFetching}
        />
      ) : (
        <div className="space-y-6" aria-busy={overview.isFetching}>
          <StaleNotice query={overview} />
          <Alert>
            <AlertTitle>
              Fonte configurada: {overview.data.data_source === "synthetic" ? "sintética" : "DataJud"}
            </AlertTitle>
            <AlertDescription>
              A última observação local indica quando estes dados foram vistos pela aplicação. Uma consulta concluída não garante que o tribunal esteja atualizado.
            </AlertDescription>
          </Alert>
          <Card>
            <CardHeader className="gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <CardTitle>Recorte dos indicadores</CardTitle>
                <CardDescription>
                  {resolvedFilterLabels(overview.data.filters).length
                    ? "Os indicadores abaixo usam estes filtros resolvidos pela API."
                    : "Todos os processos conhecidos na base local."}
                </CardDescription>
              </div>
              <Link
                to={processListHref(overview.data.filters)}
                className="text-sm font-medium text-primary underline-offset-4 hover:underline"
              >
                Ajustar filtros em Processos
              </Link>
            </CardHeader>
            <CardContent className="space-y-4">
              {resolvedFilterLabels(overview.data.filters).length ? (
                <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
                  {resolvedFilterLabels(overview.data.filters).map(([label, value]) => (
                    <div key={label}>
                      <dt className="text-xs text-muted-foreground">{label}</dt>
                      <dd className="break-all">{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
              <p className="text-sm text-muted-foreground">
                Indicadores gerados em {formatDateTime(overview.data.generated_at)}. Última observação local: {formatDateTime(overview.data.latest_observation_at)}.
              </p>
            </CardContent>
          </Card>
          {overview.data.processes.value === 0 ? (
            <EmptyState title="Nenhum processo neste recorte">
              A consulta terminou com sucesso; por isso os zeros abaixo são contagens reais desta base local. Inicie uma coleta pelo <Link to="/radar" className="font-medium text-primary underline">radar</Link> para popular a demonstração.
            </EmptyState>
          ) : null}
          <section aria-label="Indicadores da base local" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              title="Processos distintos"
              metric={overview.data.processes}
              href={processListHref(overview.data.filters)}
              detail="Abrir os processos deste recorte"
            />
            <MetricCard
              title="Representações"
              metric={overview.data.representations}
              href={processListHref(overview.data.filters)}
              detail="Capas por origem nos processos selecionados"
            />
            <MetricCard
              title="Triagem pendente"
              metric={overview.data.triage.pending}
              href={processListHref(overview.data.filters, { decision: "pending" })}
              detail="Inclui processos sem decisão registrada"
            />
            <MetricCard
              title="Triagem relevante"
              metric={overview.data.triage.relevant}
              href={processListHref(overview.data.filters, { decision: "relevant" })}
              detail="Abrir processos marcados como relevantes"
            />
            <MetricCard
              title="Triagem descartada"
              metric={overview.data.triage.discarded}
              href={processListHref(overview.data.filters, { decision: "discarded" })}
              detail="Abrir processos marcados como descartados"
            />
            <MetricCard
              title="Acompanhados ativos"
              metric={overview.data.followed_processes}
              href={processListHref(overview.data.filters, { followed: "true" })}
              detail="Abrir processos com acompanhamento ativo"
            />
            <MetricCard
              title="Novidades pendentes"
              metric={overview.data.pending_news}
              href={newsListHref(overview.data.filters)}
              detail="Ocorrências aguardando revisão"
            />
          </section>
          <div className="grid gap-6 lg:grid-cols-2">
            <CurrentSignals overview={overview.data} />
            <Themes overview={overview.data} />
          </div>
          <LatestCollections overview={overview.data} />
        </div>
      )}
    </div>
  );
}
