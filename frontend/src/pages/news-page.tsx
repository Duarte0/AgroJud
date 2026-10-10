import { useState, type FormEvent } from "react";
import { Link, useLocation, useSearchParams } from "react-router";

import { describeError } from "@/api/client";
import { useNewsList, useUpdateNewsStatus } from "@/api/queries";
import type { NewsCategory, NewsListQuery, NewsStatus, ProcessNews } from "@/api/types";
import { AppliedFilters } from "@/components/applied-filters";
import { PageHeader } from "@/components/page-header";
import { Pagination } from "@/components/pagination";
import { EmptyState, ErrorState, LoadingState, StaleNotice } from "@/components/query-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { usePageTitle } from "@/hooks/use-page-title";
import { formatCnj, formatDateTime } from "@/lib/format";
import { readPage, withPage } from "@/lib/search-params";

const PAGE_SIZE = 25;

const CATEGORY_LABELS: Record<NewsCategory, string> = {
  NEW_OBSERVATION: "Conteúdo recém-observado",
  ALTERATION_OBSERVED: "Alteração observada",
  NEW_REPRESENTATION: "Nova representação",
};

function textValue(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number") return String(value);
  return null;
}

function eventDate(item: ProcessNews): string {
  if (item.event_date) return formatDateTime(item.event_date);
  const original = textValue(item.event_date_original);
  if (original) return `Data sem normalização: ${original}`;
  if (item.event_date_status === "missing") return "Data do evento não informada pela fonte";
  return "Data do evento desconhecida";
}

function evidenceSummary(item: ProcessNews): string {
  const content = item.evidence.content;
  if (content && typeof content === "object" && !Array.isArray(content)) {
    const record = content as Record<string, unknown>;
    const name = textValue(record.nome);
    const code = textValue(record.codigo);
    return [name, code ? `código ${code}` : null].filter(Boolean).join(" · ") || "Movimento observado";
  }
  return `${item.tribunal} · origem ${item.source} · identificador ${item.source_id}`;
}

function NewsCard({ item }: { item: ProcessNews }) {
  const updateStatus = useUpdateNewsStatus();
  const location = useLocation();
  const nextStatus: NewsStatus = item.status === "pending" ? "reviewed" : "pending";

  return (
    <article className="news-row" data-testid="news-item">
      <header className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="text-base font-medium">
            <Link
              to={`/processes/${item.process_id}`}
              state={{ from: location.pathname + location.search }}
              className="cnj text-primary underline-offset-4 hover:underline"
            >
              {formatCnj(item.numero_cnj)}
            </Link>
          </h2>
          <p className="break-words text-xs text-muted-foreground">
            {item.tribunal} · origem {item.source} · representação {item.source_id}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant={item.category === "ALTERATION_OBSERVED" ? "warning" : "secondary"}>
            {CATEGORY_LABELS[item.category]}
          </Badge>
          <Badge variant={item.status === "pending" ? "outline" : "success"}>
            {item.status === "pending" ? "Pendente" : "Revisada"}
          </Badge>
        </div>
      </header>
      <div className="space-y-3">
        <p className="font-medium">{evidenceSummary(item)}</p>
        {item.category === "NEW_OBSERVATION" ? (
          <p className="text-sm text-muted-foreground">
            Conteúdo que passou a ser conhecido localmente; isso não afirma que seja um novo ato jurídico.
          </p>
        ) : null}
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs text-muted-foreground">Data original do evento</dt>
            <dd>{eventDate(item)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Primeira observação local</dt>
            <dd>{formatDateTime(item.first_observed_at)}</dd>
          </div>
        </dl>
        <details className="text-sm"><summary className="text-muted-foreground">Origem e evidência</summary><div className="mt-3 space-y-3">
        {item.category === "ALTERATION_OBSERVED" ? (
          <details className="rounded-md border px-3 py-2 text-sm">
            <summary className="cursor-pointer font-medium">Ver evidência da alteração</summary>
            <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">
              {JSON.stringify(item.evidence.alteration ?? item.evidence, null, 2)}
            </pre>
          </details>
        ) : null}
        <p className="break-words text-xs text-muted-foreground">{item.tribunal} · origem {item.source} · representação {item.source_id}</p>
        </div></details>
        <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
          <span className="text-xs text-muted-foreground">
            Proveniência local: {item.provenance === "ingestion" ? "ingestão" : "reprocessamento de quarentena"}
          </span>
          <Button
            variant={item.status === "pending" ? "default" : "outline"}
            disabled={updateStatus.isPending}
            onClick={() => updateStatus.mutate({ id: item.id, status: nextStatus })}
          >
            {item.status === "pending" ? "Marcar como revisada" : "Reabrir revisão"}
          </Button>
        </div>
        {updateStatus.error ? (
          <Alert variant="destructive" role="alert">
            <AlertTitle>A revisão não foi atualizada</AlertTitle>
            <AlertDescription>{describeError(updateStatus.error)}</AlertDescription>
          </Alert>
        ) : null}
      </div>
    </article>
  );
}

function queryStatus(value: string | null): NewsStatus | undefined {
  return value === "pending" || value === "reviewed" ? value : undefined;
}

function queryCategory(value: string | null): NewsCategory | undefined {
  return value === "NEW_OBSERVATION" ||
    value === "ALTERATION_OBSERVED" ||
    value === "NEW_REPRESENTATION"
    ? value
    : undefined;
}

function processFiltersFromUrl(params: URLSearchParams): Partial<NewsListQuery> {
  const query: Partial<NewsListQuery> = {};
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


export function NewsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const currentNumber = searchParams.get("process_number") ?? "";
  const [numberDraft, setNumberDraft] = useState({ source: currentNumber, value: currentNumber });
  const processNumberDraft = numberDraft.source === currentNumber ? numberDraft.value : currentNumber;
  const page = readPage(searchParams);
  const status = queryStatus(searchParams.get("status"));
  const category = queryCategory(searchParams.get("category"));
  const processFilters = processFiltersFromUrl(searchParams);

  const query: NewsListQuery = {
    page,
    page_size: PAGE_SIZE,
    ...processFilters,
    ...(status ? { status } : {}),
    ...(category ? { category } : {}),
  };
  const news = useNewsList(query);
  usePageTitle("Novidades");

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("page");
    setSearchParams(next);
  }

  function submitProcessFilter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilter("process_number", processNumberDraft.trim());
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Novidades"
        description="Conteúdo observado depois da referência local de cada representação. As datas do evento e da observação ficam separadas."
      />
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Filtros</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-[2fr_1fr_1fr]">
          <form className="space-y-2" onSubmit={submitProcessFilter}>
            <label htmlFor="news-process-number" className="text-sm font-medium">
              Processo por número CNJ
            </label>
            <div className="flex gap-2">
              <Input
                id="news-process-number"
                value={processNumberDraft}
                onChange={(event) => setNumberDraft({source: currentNumber, value: event.target.value})}
                placeholder="0000000-00.0000.0.00.0000"
              />
              <Button type="submit" variant="outline">Filtrar</Button>
            </div>
          </form>
          <div className="space-y-2"><p className="text-sm font-medium">Situação</p><nav aria-label="Situação das novidades" className="flex flex-wrap gap-1">{[["", "Todas"], ["pending", "Pendentes"], ["reviewed", "Revisadas"]].map(([value, label]) => {
            const next = new URLSearchParams(searchParams); if (value) next.set("status", value); else next.delete("status"); next.delete("page");
            return <Link key={value} to={`?${next}`} aria-current={(status ?? "") === value ? "page" : undefined} className="rounded-md px-3 py-2 text-sm hover:bg-primary-soft aria-[current=page]:bg-primary-soft aria-[current=page]:font-medium aria-[current=page]:text-primary-dark">{label}</Link>;
          })}</nav></div>
          <div className="space-y-2">
            <label htmlFor="news-category" className="text-sm font-medium">Categoria</label>
            <NativeSelect
              id="news-category"
              value={category ?? ""}
              onChange={(event) => setFilter("category", event.target.value)}
            >
              <option value="">Todas</option>
              {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </NativeSelect>
          </div>
        </CardContent>
      </Card>
      <AppliedFilters values={Object.fromEntries(Object.entries(processFilters).map(([key, value]) => [key, String(value)]))} onRemove={key => setFilter(key, "")} />
      <Card>
        <CardContent className="space-y-4 pt-6">
          {news.isPending ? (
            <LoadingState label="Carregando novidades…" />
          ) : !news.data ? (
            <ErrorState
              title="Não foi possível carregar as novidades"
              error={news.error}
              onRetry={() => void news.refetch()}
              retrying={news.isFetching}
            />
          ) : (
            <div className="space-y-4" aria-busy={news.isPlaceholderData}>
              <StaleNotice query={news} />
              {news.data.items.length === 0 ? (
                <EmptyState title="Nenhuma novidade neste recorte">
                  Baselines pendentes aguardam uma lista de movimentos completa. Uma falha ou resposta parcial não é tratada como ausência de novidade.
                </EmptyState>
              ) : (
                news.data.items.map((item) => <NewsCard key={item.id} item={item} />)
              )}
              <Pagination
                label="Paginação das novidades"
                page={page}
                pageSize={PAGE_SIZE}
                total={news.data.total}
                disabled={news.isPlaceholderData}
                onPageChange={(next) => setSearchParams(withPage(searchParams, next))}
              />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
