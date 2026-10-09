import { useEffect } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { ApiError, isNotFound } from "@/api/client";
import { useMovements, useProcess, useRepresentations } from "@/api/queries";
import type {
  Movement,
  MovementDiagnostic,
  ProcessDetail,
  Representation,
} from "@/api/types";
import { DescriptionList } from "@/components/description-list";
import { ProcessTriagePanel } from "@/components/process-triage-panel";
import { ProcessSignalsPanel } from "@/components/process-signals-panel";
import { ProcessWatchPanel } from "@/components/process-watch-panel";
import { PageHeader } from "@/components/page-header";
import { usePageTitle } from "@/hooks/use-page-title";
import { Pagination } from "@/components/pagination";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleNotice,
} from "@/components/query-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  captureOutcomeLabel,
  comparisonLabel,
  dateStatusLabel,
  formatCnj,
  formatCount,
  formatDateTime,
  jobStatusLabel,
  NOT_INFORMED,
} from "@/lib/format";
import { readPage, withPage } from "@/lib/search-params";
import { NotFoundPage } from "@/pages/not-found-page";

const MOVEMENT_PAGE_SIZE = 25;
const REPRESENTATION_PAGE_SIZE = 100;

function codeAndName(code: string | null, name: string | null): string {
  if (!code && !name) return NOT_INFORMED;
  if (code && name) return `${name} (${code})`;
  return name ?? code ?? NOT_INFORMED;
}

function representationLabel(representation: Representation): string {
  const parts = [
    representation.tribunal,
    representation.grau ?? "grau não informado",
    representation.court_unit_name ?? representation.court_unit_code ?? "órgão não informado",
  ];
  return parts.join(" · ");
}

/** Source instants with an ambiguous offset are flagged and keep the original value. */
function SourceInstant({
  value,
  original,
  ambiguous,
}: {
  value: string | null;
  original: string | null;
  ambiguous: boolean;
}) {
  if (!value) {
    return <>{original ? `Não normalizado; valor original da fonte: ${original}` : NOT_INFORMED}</>;
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {formatDateTime(value)}
      {ambiguous ? (
        <Badge variant="warning" title={`Valor original: ${original ?? NOT_INFORMED}`}>
          Ambígua: sem fuso na fonte
        </Badge>
      ) : null}
      {ambiguous && original ? (
        <span className="text-xs text-muted-foreground">original: {original}</span>
      ) : null}
    </span>
  );
}

function DiagnosticNote({ diagnostic }: { diagnostic: MovementDiagnostic }) {
  if (!diagnostic.available) {
    return <span>Movimentos ainda não normalizados para esta capa.</span>;
  }
  if (diagnostic.is_complete === false) {
    return (
      <span>
        Lista incompleta: {formatCount(diagnostic.rejection_count ?? 0)} movimento(s) rejeitado(s)
        na normalização.
      </span>
    );
  }
  return (
    <span>
      Lista normalizada integralmente
      {diagnostic.normalizer_version ? ` (normalizador ${diagnostic.normalizer_version})` : ""}.
    </span>
  );
}

function RepresentationCard({ representation }: { representation: Representation }) {
  const latest = representation.latest_collection;
  return (
    <Card data-testid="representation">
      <CardHeader>
        <CardTitle>{representationLabel(representation)}</CardTitle>
        <CardDescription>
          Origem {representation.source} · identificador na fonte {representation.source_id}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <DescriptionList
          items={[
            {
              term: "Classe",
              value: codeAndName(representation.class_code, representation.class_name),
            },
            {
              term: "Órgão julgador",
              value: codeAndName(representation.court_unit_code, representation.court_unit_name),
            },
            { term: "Grau", value: representation.grau ?? NOT_INFORMED },
            {
              term: "Ajuizamento (fonte)",
              value: (
                <SourceInstant
                  value={representation.source_filed_at}
                  original={representation.source_filed_at_original}
                  ambiguous={representation.source_filed_at_timezone_ambiguous}
                />
              ),
            },
            {
              term: "Atualização informada pela fonte",
              value: (
                <SourceInstant
                  value={representation.source_updated_at}
                  original={representation.source_updated_at_original}
                  ambiguous={representation.source_updated_at_timezone_ambiguous}
                />
              ),
            },
            {
              term: "Última observação local",
              value: formatDateTime(representation.last_observed_at),
            },
            {
              term: "Coleta mais recente",
              value: latest ? (
                <span>
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
                  {latest.environment === "demo" ? " · sintética" : ""}
                </span>
              ) : (
                NOT_INFORMED
              ),
            },
            {
              term: "Movimentos",
              value: <DiagnosticNote diagnostic={representation.movement_diagnostic} />,
            },
          ]}
        />
      </CardContent>
    </Card>
  );
}

function asText(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number") return String(value);
  return null;
}

function complementTexts(content: Record<string, unknown>): string[] {
  const complements = content.complementosTabelados;
  if (!Array.isArray(complements)) return [];
  return complements.map((item) => {
    if (!item || typeof item !== "object") return String(item);
    const record = item as Record<string, unknown>;
    const name = asText(record.nome);
    const description = asText(record.descricao) ?? asText(record.valor);
    return [name, description].filter(Boolean).join(": ") || JSON.stringify(record);
  });
}

function EventDate({ movement }: { movement: Movement }) {
  const original = asText(movement.source_date_original);
  if (!movement.source_date) {
    const status = movement.source_date_status;
    return (
      <span>
        {status === "unparseable" && original
          ? `Data ilegível na fonte: ${original}`
          : NOT_INFORMED}
      </span>
    );
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {formatDateTime(movement.source_date)}
      {movement.source_date_status === "timezone_ambiguous" ? (
        <Badge variant="warning" title={dateStatusLabel(movement.source_date_status)}>
          Ambígua: sem fuso na fonte
        </Badge>
      ) : null}
      {movement.source_date_status === "timezone_ambiguous" && original ? (
        <span className="text-xs text-muted-foreground">original: {original}</span>
      ) : null}
    </span>
  );
}

function TimelineItem({
  movement,
  representation,
}: {
  movement: Movement;
  representation: Representation | undefined;
}) {
  const content = movement.content as Record<string, unknown>;
  const code = asText(content.codigo);
  const name = asText(content.nome);
  const complements = complementTexts(content);
  return (
    <li
      id={`movement-${movement.occurrence_id}`}
      tabIndex={-1}
      className="relative border-l-2 border-border pb-6 pl-5 last:pb-0 focus-visible:outline-2 focus-visible:outline-ring"
      data-testid="movement"
    >
      <span
        className="absolute top-1.5 -left-1.75 size-3 rounded-full border-2 border-card bg-primary"
        aria-hidden="true"
      />
      <h3 className="font-medium wrap-break-word">
        {name ?? "Movimento sem descrição"}
        {code ? <span className="ml-1.5 text-sm font-normal text-muted-foreground">código {code}</span> : null}
      </h3>
      {complements.length > 0 ? (
        <ul className="mt-1 list-disc pl-5 text-sm text-muted-foreground">
          {complements.map((complement, index) => (
            <li key={`${index}-${complement}`}>{complement}</li>
          ))}
        </ul>
      ) : null}
      <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-muted-foreground">Data do evento</dt>
          <dd>
            <EventDate movement={movement} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Atualização da fonte</dt>
          <dd>
            {representation ? (
              <SourceInstant
                value={representation.source_updated_at}
                original={representation.source_updated_at_original}
                ambiguous={representation.source_updated_at_timezone_ambiguous}
              />
            ) : (
              NOT_INFORMED
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Observação local</dt>
          <dd>{formatDateTime(movement.first_observed_at)}</dd>
        </div>
      </dl>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Badge variant="outline">
          Origem: {representation ? representationLabel(representation) : `capa ${movement.representation_id}`}
        </Badge>
        <Badge variant={movement.comparison_result === "ALTERATION_OBSERVED" ? "warning" : "secondary"}>
          {comparisonLabel(movement.comparison_result)}
        </Badge>
        {movement.multiplicity_ordinal > 1 ? (
          <Badge variant="outline">Ocorrência nº {movement.multiplicity_ordinal}</Badge>
        ) : null}
      </div>
    </li>
  );
}

function Timeline({
  processId,
  representations,
}: {
  processId: string;
  representations: Map<string, Representation>;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = readPage(searchParams, "mov_page");
  const evidenceId = searchParams.get("evidence");
  const movements = useMovements(processId, page, evidenceId ?? undefined);

  useEffect(() => {
    if (!evidenceId || !movements.data) return;
    document.getElementById(`movement-${evidenceId}`)?.scrollIntoView?.({ block: "center" });
  }, [evidenceId, movements.data]);

  return (
    <Card id="timeline">
      <CardHeader>
        <CardTitle>Linha do tempo</CardTitle>
        <CardDescription>
          Movimentos por data do evento (sem data ao final). Cada item mantém sua capa de origem e
          três datas distintas.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {movements.isPending ? (
          <LoadingState label="Carregando movimentos…" />
        ) : !movements.data ? (
          <ErrorState
            title="Não foi possível carregar os movimentos"
            error={movements.error}
            onRetry={() => void movements.refetch()}
            retrying={movements.isFetching}
          />
        ) : (
          <div className="space-y-4" aria-busy={movements.isPlaceholderData}>
            <StaleNotice query={movements} />
            {movements.data.diagnostics.some((diagnostic) => diagnostic.is_complete === false) ? (
              <Alert variant="warning">
                <AlertTitle>Há capas com lista de movimentos incompleta</AlertTitle>
                <AlertDescription>
                  Movimentos rejeitados na normalização não aparecem nesta linha do tempo.
                </AlertDescription>
              </Alert>
            ) : null}
            {movements.data.items.length === 0 ? (
              <EmptyState
                title={
                  movements.data.total > 0
                    ? "Esta página não contém movimentos"
                    : "Nenhum movimento normalizado"
                }
              >
                A ausência de movimentos na base local não indica encerramento nem sigilo.
              </EmptyState>
            ) : (
              <ol aria-label="Movimentos do processo" className="pt-1">
                {movements.data.items.map((movement) => (
                  <TimelineItem
                    key={movement.occurrence_id}
                    movement={movement}
                    representation={representations.get(movement.representation_id)}
                  />
                ))}
              </ol>
            )}
            <Pagination
              label="Paginação dos movimentos"
              page={page}
              pageSize={MOVEMENT_PAGE_SIZE}
              total={movements.data.total}
              disabled={movements.isPlaceholderData}
              onPageChange={(next) => setSearchParams(withPage(searchParams, next, "mov_page"))}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ProcessView({ process }: { process: ProcessDetail }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const representationPage = readPage(searchParams, "rep_page");
  const representations = useRepresentations(process.id, representationPage);
  const byId = new Map((representations.data?.items ?? []).map((item) => [item.id, item]));

  return (
    <div className="space-y-6">
      <PageHeader
        title={<span className="tabular-nums">{formatCnj(process.numero_cnj)}</span>}
        description="Processo agrupado por número CNJ; capas de origens distintas são exibidas separadamente."
      />
      <Card>
        <CardContent>
          <DescriptionList
            items={[
              { term: "Registrado localmente em", value: formatDateTime(process.created_at) },
              { term: "Última observação local", value: formatDateTime(process.latest_observed_at) },
              { term: "Capas", value: formatCount(process.representation_count) },
            ]}
          />
        </CardContent>
      </Card>

      <ProcessWatchPanel processId={process.id} />
      <ProcessTriagePanel processId={process.id} initialTriage={process.triage} />
      <ProcessSignalsPanel processId={process.id} />

      <section aria-labelledby="capas-title" className="space-y-3">
        <h2 id="capas-title" className="text-lg font-semibold">
          Capas por origem
        </h2>
        {representations.isPending ? (
          <LoadingState label="Carregando capas…" />
        ) : !representations.data ? (
          <ErrorState
            title="Não foi possível carregar as capas"
            error={representations.error}
            onRetry={() => void representations.refetch()}
            retrying={representations.isFetching}
          />
        ) : (
          <div className="space-y-4">
            <StaleNotice query={representations} />
            {representations.data.items.length === 0 ? (
              <EmptyState title="Nenhuma capa registrada para este processo" />
            ) : (
              representations.data.items.map((representation) => (
                <RepresentationCard key={representation.id} representation={representation} />
              ))
            )}
            {representations.data.total > REPRESENTATION_PAGE_SIZE ? (
              <Pagination
                label="Paginação das capas"
                page={representationPage}
                pageSize={REPRESENTATION_PAGE_SIZE}
                total={representations.data.total}
                onPageChange={(next) =>
                  setSearchParams(withPage(searchParams, next, "rep_page"))
                }
              />
            ) : null}
          </div>
        )}
      </section>

      <Timeline processId={process.id} representations={byId} />
    </div>
  );
}

export function ProcessDetailPage() {
  const { processId = "" } = useParams();
  const process = useProcess(processId);
  usePageTitle(process.data ? formatCnj(process.data.numero_cnj) : "Processo");

  if (process.isPending) return <LoadingState label="Carregando processo…" />;
  if (!process.data) {
    if (
      isNotFound(process.error) ||
      (process.error instanceof ApiError && process.error.status === 422)
    ) {
      return (
        <NotFoundPage
          title="Processo não encontrado"
          description="Não existe processo com este identificador na base local deste ambiente."
          backTo="/processes"
          backLabel="Ver processos"
        />
      );
    }
    return (
      <ErrorState
        title="Não foi possível carregar o processo"
        error={process.error}
        onRetry={() => void process.refetch()}
        retrying={process.isFetching}
      />
    );
  }
  return (
    <div className="space-y-4">
      <StaleNotice query={process} />
      <ProcessView process={process.data} />
    </div>
  );
}
