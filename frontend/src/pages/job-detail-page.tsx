import { useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import { ApiError, describeError, isNotFound } from "@/api/client";
import { queryKeys, useJob, useJobCommand } from "@/api/queries";
import type { JobCommandResult, JobDetail } from "@/api/types";
import { useCurrentEnvironment } from "@/app/environment-context";
import { ConfirmAction } from "@/components/confirm-action";
import { ContextBackLink } from "@/components/context-back-link";
import { DescriptionList } from "@/components/description-list";
import { JobStatusBadge } from "@/components/job-status-badge";
import { PageHeader } from "@/components/page-header";
import { usePageTitle } from "@/hooks/use-page-title";
import { ErrorState, LoadingState, StaleNotice } from "@/components/query-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  formatCalendarDate,
  formatCount,
  formatDateTime,
  jobKindLabel,
  attemptOutcomeLabel,
  jobStatusLabel,
  NOT_INFORMED,
  reasonLabel,
} from "@/lib/format";
import { availableJobActions, type JobAction } from "@/lib/job-actions";
import { isActiveJob, lastActivityAt, readJobProgress, type JobProgress } from "@/lib/job-progress";
import { NotFoundPage } from "@/pages/not-found-page";

type LocationState = { created?: boolean; reused?: boolean } | null;

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return NOT_INFORMED;
  if (typeof value === "string" || typeof value === "number") return String(value);
  return JSON.stringify(value);
}

function StatusExplanation({ job }: { job: JobDetail }) {
  const reason = reasonLabel(job.reason);
  const lastError = [...job.attempts].reverse().find((attempt) => attempt.error_summary);
  if (job.status === "failed") {
    return (
      <Alert variant="destructive" data-state="failed">
        <AlertTitle>A coleta falhou{reason ? `: ${reason}` : ""}</AlertTitle>
        <AlertDescription>
          {lastError ? (
            <p>
              Último erro ({lastError.error_code ?? "sem código"}): {lastError.error_summary}
            </p>
          ) : null}
          <p>
            Uma falha da fonte não significa ausência de processos. Páginas confirmadas antes da
            falha permanecem na base local.
          </p>
        </AlertDescription>
      </Alert>
    );
  }
  if (job.status === "partial") {
    return (
      <Alert variant="warning" data-state="partial">
        <AlertTitle>Resultado parcial{reason ? `: ${reason}` : ""}</AlertTitle>
        <AlertDescription>
          <p>
            Os registros confirmados estão disponíveis, mas a consulta não cobre todo o recorte.
          </p>
        </AlertDescription>
      </Alert>
    );
  }
  if (job.status === "cancelled") {
    return (
      <Alert data-state="cancelled">
        <AlertTitle>Coleta cancelada</AlertTitle>
        <AlertDescription>
          O cancelamento não desfaz resultados confirmados antes dele.
        </AlertDescription>
      </Alert>
    );
  }
  if (job.cancel_requested && isActiveJob(job.status)) {
    return (
      <Alert variant="info" data-state="cancel-requested">
        <AlertTitle>Cancelamento solicitado</AlertTitle>
        <AlertDescription>
          O worker encerrará a coleta na próxima verificação; páginas já confirmadas permanecem.
        </AlertDescription>
      </Alert>
    );
  }
  if (job.status === "retry_wait") {
    return (
      <Alert variant="warning" data-state="retry-wait">
        <AlertTitle>Aguardando nova tentativa{reason ? `: ${reason}` : ""}</AlertTitle>
        <AlertDescription>Próxima tentativa prevista para {formatDateTime(job.next_attempt_at)}.</AlertDescription>
      </Alert>
    );
  }
  return null;
}

function SourceTotal({ progress }: { progress: JobProgress }) {
  const total = progress.sourceTotal;
  if (total.kind === "exact") return <>{formatCount(total.value)} (exato)</>;
  if (total.kind === "lower_bound") {
    return (
      <>
        pelo menos {formatCount(total.value)} (relação “{total.relation}”, total não exato)
      </>
    );
  }
  return <>{NOT_INFORMED}</>;
}

function ProgressCard({ job }: { job: JobDetail }) {
  const progress = readJobProgress(job.coverage);
  const coverageLabel =
    progress.coverage === "complete"
      ? "Completa para a consulta"
      : progress.coverage === "partial"
        ? "Parcial"
        : "Ainda não determinada";
  return (
    <Card>
      <CardHeader>
        <CardTitle>Progresso confirmado</CardTitle>
        <CardDescription>
          Contadores gravados junto ao checkpoint; nada aqui é estimado.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {progress.percent !== null ? (
          <div className="space-y-1">
            <div className="flex justify-between text-sm">
              <span>Registros confirmados sobre o total exato da fonte</span>
              <span className="tabular-nums">{progress.percent}%</span>
            </div>
            <div
              role="progressbar"
              aria-label="Progresso sobre o total exato da fonte"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress.percent}
              className="h-2 overflow-hidden rounded-full bg-muted"
            >
              <div className="h-full bg-primary" style={{ width: `${progress.percent}%` }} />
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground" data-testid="no-percentage">
            Sem porcentagem: o total remoto não é exato ou não foi informado.
          </p>
        )}
        <DescriptionList
          items={[
            {
              term: "Registros confirmados",
              value: (
                <span data-testid="hits-confirmed">
                  {formatCount(progress.hitsConfirmed ?? 0)}
                  {progress.budgetLimit !== null
                    ? ` de limite ${formatCount(progress.budgetLimit)}`
                    : ""}
                </span>
              ),
            },
            { term: "Total informado pela fonte", value: <SourceTotal progress={progress} /> },
            { term: "Páginas confirmadas", value: formatCount(progress.pagesConfirmed ?? 0) },
            { term: "Válidos", value: formatCount(progress.validHits ?? 0) },
            {
              term: "Rejeitados",
              value: (
                <span data-testid="rejected-hits">
                  {formatCount(progress.rejectedHits ?? 0)} (quarentena:{" "}
                  {formatCount(progress.quarantineRecords ?? 0)})
                </span>
              ),
            },
            {
              term: "Novos / atualizados / sem alteração",
              value: `${formatCount(progress.newCount ?? 0)} / ${formatCount(progress.updatedCount ?? 0)} / ${formatCount(progress.unchangedCount ?? 0)}`,
            },
            { term: "Cobertura", value: <span data-testid="coverage">{coverageLabel}</span> },
            {
              term: "Dados persistidos",
              value:
                progress.hasPersistedData === null
                  ? NOT_INFORMED
                  : progress.hasPersistedData
                    ? "Sim"
                    : "Não",
            },
            {
              term: "Última atividade",
              value: <span data-testid="last-activity">{formatDateTime(lastActivityAt(job))}</span>,
            },
          ]}
        />
      </CardContent>
    </Card>
  );
}

function CriteriaCard({ job }: { job: JobDetail }) {
  const criteria = job.criteria;
  const snapshot =
    criteria.catalog_snapshot && typeof criteria.catalog_snapshot === "object"
      ? (criteria.catalog_snapshot as Record<string, unknown>)
      : null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Critérios efetivos</CardTitle>
      </CardHeader>
      <CardContent>
        <DescriptionList
          items={[
            { term: "Tipo", value: jobKindLabel(job.kind) },
            { term: "Tribunal / fonte", value: `${job.tribunal} · ${job.source}` },
            {
              term: "Preset",
              value:
                typeof criteria.preset_id === "string"
                  ? `${criteria.preset_id} · versão ${text(criteria.preset_version)}`
                  : NOT_INFORMED,
            },
            {
              term: "Ajuizamento",
              value:
                typeof criteria.filed_from === "string" || typeof criteria.filed_to === "string"
                  ? `de ${formatCalendarDate(criteria.filed_from as string | null)} até antes de ${formatCalendarDate(criteria.filed_to as string | null)}`
                  : NOT_INFORMED,
            },
            {
              term: "Catálogo",
              value: snapshot ? `versão ${text(snapshot.catalog_version)}` : NOT_INFORMED,
            },
            {
              term: "Número processual",
              value: typeof criteria.process_number === "string" ? criteria.process_number : "—",
            },
          ]}
        />
      </CardContent>
    </Card>
  );
}

function AttemptsCard({ job }: { job: JobDetail }) {
  const events = [...job.events].reverse().slice(0, 15);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Tentativas e eventos</CardTitle>
        <CardDescription>
          {formatCount(job.attempt_count)} tentativa(s) · ciclo {formatCount(job.retry_cycle)} ·
          checkpoint na página {formatCount(job.checkpoint.next_page)} (revisão{" "}
          {formatCount(job.checkpoint.revision)})
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {job.attempts.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nenhuma tentativa registrada ainda.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>Início</TableHead>
                <TableHead>Fim</TableHead>
                <TableHead>Resultado</TableHead>
                <TableHead>Erro</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {job.attempts.map((attempt) => (
                <TableRow key={attempt.id}>
                  <TableCell className="tabular-nums">{attempt.attempt_number}</TableCell>
                  <TableCell className="whitespace-nowrap">{formatDateTime(attempt.started_at)}</TableCell>
                  <TableCell className="whitespace-nowrap">{formatDateTime(attempt.finished_at)}</TableCell>
                  <TableCell>{attemptOutcomeLabel(attempt.outcome)}</TableCell>
                  <TableCell className="min-w-48 text-muted-foreground">
                    {attempt.error_code
                      ? `${attempt.error_code}: ${attempt.error_summary ?? ""}`
                      : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <div>
          <h3 className="mb-2 text-sm font-semibold">Eventos recentes</h3>
          {events.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nenhum evento registrado.</p>
          ) : (
            <ol className="space-y-1.5 text-sm">
              {events.map((event) => (
                <li key={event.event_number} className="flex flex-wrap gap-x-3">
                  <span className="whitespace-nowrap text-muted-foreground tabular-nums">
                    {formatDateTime(event.created_at)}
                  </span>
                  <span className="font-medium">{event.event_type}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function JobActions({ job }: { job: JobDetail }) {
  const command = useJobCommand(job.id);
  const navigate = useNavigate();
  const inFlight = useRef(false);
  const [confirmed, setConfirmed] = useState<JobCommandResult | null>(null);
  const actions = availableJobActions(job);
  const [confirmAction, setConfirmAction] = useState<JobAction | null>(null);

  async function run(action: JobAction) {
    if (inFlight.current) return;
    inFlight.current = true;
    setConfirmed(null);
    try {
      const result = await command.mutateAsync(action.command);
      setConfirmed(result);
      if (result.job_id !== job.id) navigate(`/jobs/${result.job_id}`);
    } catch {
      // Rendered from command.error; the persisted state is reloaded by polling/invalidation.
    } finally {
      inFlight.current = false;
    }
  }

  if (actions.length === 0 && !command.isError && !confirmed) return null;
  return (
    <div className="space-y-3">
      <ConfirmAction open={confirmAction !== null} onOpenChange={open => { if (!open) setConfirmAction(null); }} title={`${confirmAction?.label ?? "Confirmar ação"}?`} description={confirmAction?.description ?? ""} confirmLabel={confirmAction?.label ?? "Confirmar"} onConfirm={() => { if (confirmAction) void run(confirmAction); setConfirmAction(null); }} />
      <div className="flex flex-wrap gap-2">
        {actions.map((action) => (
          <Button
            key={action.command}
            variant={action.variant}
            disabled={command.isPending}
            onClick={() => { if (action.command === "cancel" || action.command === "restart-scan") setConfirmAction(action); else void run(action); }}
            title={action.description}
          >
            {command.isPending && command.variables === action.command ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : null}
            {command.isPending && command.variables === action.command
              ? action.pendingLabel
              : action.label}
          </Button>
        ))}
      </div>
      {actions.some((action) => action.command === "cancel") ? (
        <p className="text-xs text-muted-foreground">
          Cancelar não desfaz resultados de páginas já confirmadas.
        </p>
      ) : null}
      <div aria-live="polite">
        {confirmed ? (
          <p className="text-sm text-muted-foreground" data-testid="command-confirmed">
            Comando confirmado. Estado: {jobStatusLabel(confirmed.status)}
            {confirmed.cancel_requested && confirmed.status !== "cancelled"
              ? " (cancelamento solicitado)"
              : ""}
            .
          </p>
        ) : null}
      </div>
      {command.isError ? (
        <Alert variant="destructive" role="alert">
          <AlertTitle>O comando não foi aplicado</AlertTitle>
          <AlertDescription>{describeError(command.error)}</AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}

function JobView({ job }: { job: JobDetail }) {
  const location = useLocation();
  const state = location.state as LocationState;
  const active = isActiveJob(job.status);

  return (
    <div className="space-y-6">
      <ContextBackLink fallback="/jobs" label="Voltar às coletas" />
      <PageHeader
        title={
          <span className="flex flex-wrap items-center gap-3">
            Coleta de {formatDateTime(job.created_at)}
            <JobStatusBadge status={job.status} />
          </span>
        }
        description={`${jobKindLabel(job.kind)} · ${job.environment === "demo" ? "ambiente demo (sintético)" : "ambiente real"}`}
        actions={
          <Button asChild variant="outline">
            <Link to={`/processes?collection_id=${job.collection_id}`}>
              Ver processos desta coleta
            </Link>
          </Button>
        }
      />
      {state?.created ? (
        <Alert variant="info">
          <AlertTitle>
            {state.reused
              ? "Já havia uma coleta equivalente ativa; ela foi reaproveitada."
              : "Coleta registrada e enfileirada pelo backend."}
          </AlertTitle>
        </Alert>
      ) : null}
      <p
        aria-live="polite"
        className="flex items-center gap-2 text-sm text-muted-foreground"
        data-testid="polling-status"
      >
        {active ? (
          <>
            <RefreshCw className="size-4 animate-spin" aria-hidden="true" />
            Atualizando o andamento a cada 3 segundos.
          </>
        ) : (
          <>Estado final: {jobStatusLabel(job.status)}. Atualização automática encerrada.</>
        )}
      </p>
      <StatusExplanation job={job} />
      <JobActions job={job} />
      <ProgressCard job={job} />
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <CriteriaCard job={job} />
        <Card>
          <CardHeader>
            <CardTitle>Datas</CardTitle>
          </CardHeader>
          <CardContent>
            <DescriptionList
              className="lg:grid-cols-2"
              items={[
                { term: "Criada", value: formatDateTime(job.created_at) },
                { term: "Iniciada", value: formatDateTime(job.started_at) },
                { term: "Finalizada", value: formatDateTime(job.finished_at) },
                {
                  term: "Próxima tentativa",
                  value: active ? formatDateTime(job.next_attempt_at) : "—",
                },
              ]}
            />
          </CardContent>
        </Card>
      </div>
      <details className="rounded-lg border bg-card p-5">
        <summary className="font-medium">Tentativas e detalhes técnicos <span className="text-xs font-normal text-muted-foreground">· {job.attempt_count} tentativa(s)</span></summary>
        <p className="my-4 break-all text-xs text-muted-foreground">ID da coleta: {job.id}</p>
        <AttemptsCard job={job} />
      </details>
    </div>
  );
}

export function JobDetailPage() {
  const { jobId = "" } = useParams();
  const { environment } = useCurrentEnvironment();
  const client = useQueryClient();
  const job = useJob(jobId);
  usePageTitle("Coleta");

  const status = job.data?.status;
  useEffect(() => {
    // Results become visible once the job stops; refresh local process lists then.
    if (status && !isActiveJob(status)) {
      void client.invalidateQueries({ queryKey: queryKeys.processes(environment) });
    }
  }, [client, environment, status]);

  if (job.isPending) return <LoadingState label="Carregando coleta…" />;
  if (!job.data) {
    if (isNotFound(job.error) || (job.error instanceof ApiError && job.error.status === 422)) {
      return (
        <NotFoundPage
          title="Coleta não encontrada"
          description="Não existe coleta com este identificador neste ambiente. Ela pode ter sido removida ou o link está incorreto."
          backTo="/jobs"
          backLabel="Ver coletas"
        />
      );
    }
    return (
      <ErrorState
        title="Não foi possível carregar a coleta"
        error={job.error}
        onRetry={() => void job.refetch()}
        retrying={job.isFetching}
      />
    );
  }
  return (
    <div className="space-y-4">
      <StaleNotice query={job} />
      <JobView job={job.data} />
    </div>
  );
}
