import { Link, useLocation, useSearchParams } from "react-router";

import { useJobList } from "@/api/queries";
import type { JobKind, JobStatus, JobSummary } from "@/api/types";
import { JobStatusBadge } from "@/components/job-status-badge";
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
  formatCount,
  formatDateTime,
  JOB_KINDS,
  JOB_STATUSES,
  jobKindLabel,
  jobStatusLabel,
  reasonLabel,
} from "@/lib/format";
import { readJobProgress } from "@/lib/job-progress";
import { readEnum, readPage, withFilters, withPage } from "@/lib/search-params";

const PAGE_SIZE = 25;

function JobsTable({ jobs }: { jobs: JobSummary[] }) {
  const location = useLocation();
  return (
    <Table>
      <TableCaption className="sr-only">Coletas ordenadas da mais recente para a mais antiga</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Criada em</TableHead>
          <TableHead>Tipo</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead className="text-right">Registros confirmados</TableHead>
          <TableHead>Motivo</TableHead>
          <TableHead>Ambiente</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {jobs.map((job) => {
          const progress = readJobProgress(job.coverage);
          return (
            <TableRow key={job.id}>
              <TableCell className="whitespace-nowrap">
                <Link
                  to={`/jobs/${job.id}`}
                  state={{ from: location.pathname + location.search }}
                  className="font-medium text-primary underline-offset-4 hover:underline"
                >
                  {formatDateTime(job.created_at)}
                </Link>
              </TableCell>
              <TableCell>{jobKindLabel(job.kind)}</TableCell>
              <TableCell>
                <JobStatusBadge status={job.status} />
                {job.cancel_requested && job.status !== "cancelled" ? (
                  <span className="ml-2 text-xs text-muted-foreground">cancelamento solicitado</span>
                ) : null}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {formatCount(progress.hitsConfirmed ?? 0)}
              </TableCell>
              <TableCell className="text-muted-foreground">{reasonLabel(job.reason) ?? "—"}</TableCell>
              <TableCell>{job.environment === "demo" ? "Demo" : "Real"}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export function JobsPage() {
  usePageTitle("Coletas");
  const [searchParams, setSearchParams] = useSearchParams();
  const page = readPage(searchParams);
  const status = readEnum<JobStatus>(searchParams, "status", JOB_STATUSES);
  const kind = readEnum<JobKind>(searchParams, "kind", JOB_KINDS);
  const jobs = useJobList({ page, page_size: PAGE_SIZE, status, kind });
  const filtered = status !== undefined || kind !== undefined;

  return (
    <>
      <PageHeader
        title="Coletas"
        description="Execuções persistidas. Coletas ativas são atualizadas a cada 3 segundos."
        actions={
          <Button asChild>
            <Link to="/radar">Nova coleta</Link>
          </Button>
        }
      />
      <Card>
        <CardContent className="space-y-4">
          <form
            role="search"
            aria-label="Filtrar coletas"
            className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
            onSubmit={(event) => event.preventDefault()}
          >
            <div className="space-y-1.5">
              <Label htmlFor="job-status">Estado</Label>
              <NativeSelect
                id="job-status"
                value={status ?? ""}
                onChange={(event) =>
                  setSearchParams(withFilters(searchParams, { status: event.target.value }))
                }
              >
                <option value="">Todos</option>
                {JOB_STATUSES.map((value) => (
                  <option key={value} value={value}>
                    {jobStatusLabel(value)}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="job-kind">Tipo</Label>
              <NativeSelect
                id="job-kind"
                value={kind ?? ""}
                onChange={(event) =>
                  setSearchParams(withFilters(searchParams, { kind: event.target.value }))
                }
              >
                <option value="">Todos</option>
                {JOB_KINDS.map((value) => (
                  <option key={value} value={value}>
                    {jobKindLabel(value)}
                  </option>
                ))}
              </NativeSelect>
            </div>
            <Button
              type="button"
              variant="ghost"
              disabled={!filtered}
              onClick={() => setSearchParams(new URLSearchParams())}
            >
              Limpar filtros
            </Button>
          </form>

          {jobs.isPending ? (
            <LoadingState label="Carregando coletas…" />
          ) : !jobs.data ? (
            <ErrorState
              title="Não foi possível carregar as coletas"
              error={jobs.error}
              onRetry={() => void jobs.refetch()}
              retrying={jobs.isFetching}
            />
          ) : (
            <div className="space-y-4" aria-busy={jobs.isPlaceholderData}>
              <StaleNotice query={jobs} />
              {jobs.data.items.length === 0 ? (
                <EmptyState
                  title={
                    jobs.data.total > 0
                      ? "Esta página não contém coletas"
                      : filtered
                        ? "Nenhuma coleta corresponde aos filtros"
                        : "Nenhuma coleta registrada"
                  }
                >
                  {filtered ? "Altere ou limpe os filtros." : "Inicie uma coleta pelo radar."}
                </EmptyState>
              ) : (
                <JobsTable jobs={jobs.data.items} />
              )}
              <Pagination
                label="Paginação das coletas"
                page={page}
                pageSize={PAGE_SIZE}
                total={jobs.data.total}
                disabled={jobs.isPlaceholderData}
                onPageChange={(next) => setSearchParams(withPage(searchParams, next))}
              />
            </div>
          )}
        </CardContent>
      </Card>
    </>
  );
}
