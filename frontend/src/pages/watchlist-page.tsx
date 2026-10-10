import { Link, useLocation, useNavigate, useSearchParams } from "react-router";

import { describeError } from "@/api/client";
import { useRefreshProcess, useSetProcessWatch, useWatchlist } from "@/api/queries";
import { useCurrentEnvironment } from "@/app/environment-context";
import type { WatchlistItem } from "@/api/types";
import { ConfirmAction } from "@/components/confirm-action";
import { PageHeader } from "@/components/page-header";
import { ProcessRefreshStatus } from "@/components/process-refresh-status";
import { EmptyState, ErrorState, LoadingState, StaleNotice } from "@/components/query-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Pagination } from "@/components/pagination";
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
import { formatCnj, formatDateTime } from "@/lib/format";
import { readPage, withPage } from "@/lib/search-params";

const PAGE_SIZE = 25;

function WatchlistRow({ item }: { item: WatchlistItem }) {
  const navigate = useNavigate();
  const location = useLocation();
  const environment = useCurrentEnvironment();
  const refresh = useRefreshProcess(item.process_id);
  const setWatch = useSetProcessWatch(item.process_id);
  const actionError = refresh.error ?? setWatch.error;

  function update() {
    refresh.mutate(undefined, {
      onSuccess: (accepted) => void navigate(`/jobs/${accepted.job_id}`),
    });
  }

  return (
    <TableRow data-testid="watchlist-entry">
      <TableCell data-label="Processo">
        <div className="space-y-1">
          <Link
            to={`/processes/${item.process_id}`}
            state={{ from: location.pathname + location.search }}
            className="cnj font-medium text-primary underline-offset-4 hover:underline"
          >
            {formatCnj(item.numero_cnj)}
          </Link>
          <div className="text-xs text-muted-foreground">
            Incluído em {formatDateTime(item.included_at)}
          </div>
          <div className="text-xs text-muted-foreground">
            Próxima atualização diária: {formatDateTime(item.next_run_at)}
          </div>
          {item.last_schedule?.missed_from ? (
            <div className="text-xs text-warning-foreground">
              Intervalo perdido: {item.last_schedule.missed_from} a {item.last_schedule.missed_through}
            </div>
          ) : null}
          {item.last_schedule?.status === "pending" ? (
            <div className="text-xs text-muted-foreground">Aguardando o job anterior deste alvo.</div>
          ) : null}
        </div>
      </TableCell>
      <TableCell data-label="Última consulta por número">
        <ProcessRefreshStatus result={item.last_refresh} />
      </TableCell>
      <TableCell data-label="Ações">
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={update}
            disabled={refresh.isPending || !environment.source_enabled}
            title={environment.source_enabled ? undefined : (environment.source_disabled_reason ?? undefined)}
          >
            {refresh.isPending ? "Enfileirando…" : "Atualizar"}
          </Button>
          <ConfirmAction title="Remover acompanhamento?" description="O processo e seu histórico serão preservados. Novas atualizações diárias serão interrompidas; uma coleta já iniciada pode terminar." confirmLabel="Remover acompanhamento" onConfirm={() => setWatch.mutate(false)}>
            <Button variant="outline" disabled={setWatch.isPending}>{setWatch.isPending ? "Removendo…" : "Remover"}</Button>
          </ConfirmAction>
        </div>
        {actionError ? (
          <Alert variant="destructive" role="alert" className="mt-2">
            <AlertTitle>A ação não foi concluída</AlertTitle>
            <AlertDescription>{describeError(actionError)}</AlertDescription>
          </Alert>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

export function WatchlistPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const page = readPage(searchParams);
  const watchlist = useWatchlist({ page, page_size: PAGE_SIZE });
  usePageTitle("Acompanhados");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Acompanhados"
        description="Processos locais com atualização diária às 06h e comando manual por número CNJ."
      />
      <Card>
        <CardContent className="space-y-4">
          {watchlist.isPending ? (
            <LoadingState label="Carregando processos acompanhados…" />
          ) : !watchlist.data ? (
            <ErrorState
              title="Não foi possível carregar os acompanhados"
              error={watchlist.error}
              onRetry={() => void watchlist.refetch()}
              retrying={watchlist.isFetching}
            />
          ) : (
            <div className="space-y-4" aria-busy={watchlist.isPlaceholderData}>
              <StaleNotice query={watchlist} />
              {watchlist.data.items.length === 0 ? (
                <EmptyState title="Nenhum processo acompanhado">
                  Inclua um processo pela tela de detalhe para começar a lista.
                </EmptyState>
              ) : (
                <Table className="responsive-records">
                  <TableCaption className="sr-only">
                    Processos acompanhados e resultado da última consulta por número
                  </TableCaption>
                <TableHeader className="sr-only md:not-sr-only">
                    <TableRow>
                      <TableHead>Processo</TableHead>
                      <TableHead>Última consulta por número</TableHead>
                      <TableHead>Ações</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {watchlist.data.items.map((item) => (
                      <WatchlistRow key={item.process_id} item={item} />
                    ))}
                  </TableBody>
                </Table>
              )}
              <Pagination
                label="Paginação dos acompanhados"
                page={page}
                pageSize={PAGE_SIZE}
                total={watchlist.data.total}
                disabled={watchlist.isPlaceholderData}
                onPageChange={(next) => setSearchParams(withPage(searchParams, next))}
              />
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
