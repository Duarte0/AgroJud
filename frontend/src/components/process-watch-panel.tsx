import { useNavigate } from "react-router";

import { describeError } from "@/api/client";
import { useProcessWatch, useRefreshProcess, useSetProcessWatch } from "@/api/queries";
import { ConfirmAction } from "@/components/confirm-action";
import { useCurrentEnvironment } from "@/app/environment-context";
import { ProcessRefreshStatus } from "@/components/process-refresh-status";
import { ErrorState, LoadingState, StaleNotice } from "@/components/query-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";

export function ProcessWatchPanel({ processId, section = "controls" }: { processId: string; section?: "controls" | "history" | "baselines" }) {
  const environment = useCurrentEnvironment();
  const navigate = useNavigate();
  const watch = useProcessWatch(processId);
  const setWatch = useSetProcessWatch(processId);
  const refresh = useRefreshProcess(processId);
  const mutationError = setWatch.error ?? refresh.error;

  function toggleWatch() {
    if (!watch.data) return;
    setWatch.mutate(!watch.data.active);
  }

  function refreshProcess() {
    refresh.mutate(undefined, {
      onSuccess: (accepted) => void navigate(`/jobs/${accepted.job_id}`),
    });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{section === "history" ? "Histórico de acompanhamento" : section === "baselines" ? "Referência histórica por representação" : "Acompanhamento"}</CardTitle>
        <CardDescription hidden={section !== "controls"}>
          Atualize este processo por número CNJ fora da janela de descoberta. A triagem permanece
          independente do acompanhamento.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {watch.isPending ? (
          <LoadingState label="Carregando acompanhamento…" />
        ) : !watch.data ? (
          <ErrorState
            title="Não foi possível carregar o acompanhamento"
            error={watch.error}
            onRetry={() => void watch.refetch()}
            retrying={watch.isFetching}
          />
        ) : (
          <>
            <StaleNotice query={watch} />
            {section === "controls" ? <>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">
                {watch.data.active ? "Acompanhamento ativo" : "Não acompanhado"}
              </span>
              {watch.data.active && watch.data.included_at ? (
                <span className="text-sm text-muted-foreground">
                  incluído em {formatDateTime(watch.data.included_at)}
                </span>
              ) : null}
              {!watch.data.active && watch.data.removed_at ? (
                <span className="text-sm text-muted-foreground">
                  removido em {formatDateTime(watch.data.removed_at)}
                </span>
              ) : null}
            </div>

            <div className="flex flex-wrap gap-2">
              {watch.data.active ? <ConfirmAction title="Remover acompanhamento?" description="O processo e seu histórico serão preservados. Novas atualizações diárias serão interrompidas; uma coleta já iniciada pode terminar." confirmLabel="Remover acompanhamento" onConfirm={toggleWatch}>
                <Button variant="outline" disabled={setWatch.isPending}>{setWatch.isPending ? "Removendo…" : "Remover acompanhamento"}</Button>
              </ConfirmAction> : <Button onClick={toggleWatch} disabled={setWatch.isPending}>{setWatch.isPending ? "Incluindo…" : "Acompanhar processo"}</Button>}

              {watch.data.active ? (
                <Button onClick={refreshProcess} disabled={refresh.isPending || !environment.source_enabled}>
                  {refresh.isPending ? "Enfileirando…" : "Atualizar processo"}
                </Button>
              ) : null}
            </div>

            <div className="space-y-1">
              <h3 className="text-sm font-semibold">Resultado da última consulta por número</h3>
              <ProcessRefreshStatus result={watch.data.last_refresh} />
            </div>

            {!environment.source_enabled ? <p role="status" className="text-sm text-warning-foreground">{environment.source_disabled_reason ?? "Fonte desabilitada neste ambiente."}</p> : null}
            </> : null}
            {section === "baselines" && watch.data.active ? (
              <section className="space-y-2" aria-labelledby="watch-baselines-title">
                <h3 id="watch-baselines-title" className="text-sm font-semibold">
                  Situação por representação
                </h3>
                {watch.data.baselines.length === 0 ? (
                  <p className="text-sm text-muted-foreground">
                    Nenhuma representação local ainda. A primeira captura completa estabelecerá a referência.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {watch.data.baselines.map((baseline) => (
                      <li
                        key={baseline.representation_id}
                        data-testid="watch-baseline"
                        className="rounded-md border px-3 py-2 text-sm"
                      >
                        <div className="font-medium">
                          {baseline.tribunal} · {baseline.source}
                        </div>
                        {baseline.state === "pending" ? (
                          <p className="text-muted-foreground">
                            Baseline pendente. A primeira lista de movimentos completa será incorporada sem gerar novidades históricas.
                          </p>
                        ) : (
                          <p className="text-muted-foreground">
                            Referência estabelecida em {formatDateTime(baseline.established_at)}.
                          </p>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            ) : null}

            {section === "history" ? (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold">Eventos registrados</h3>
              {watch.data.history.length === 0 ? (
                <p className="text-sm text-muted-foreground">Sem alterações registradas.</p>
              ) : (
                <ol className="space-y-1 text-sm text-muted-foreground">
                  {watch.data.history.map((event) => (
                    <li key={event.id} data-testid="watch-history-entry">
                      {event.action === "included" ? "Incluído" : "Removido"} em{" "}
                      {formatDateTime(event.created_at)}
                    </li>
                  ))}
                </ol>
              )}
            </div>            ) : null}

          </>
        )}
        {mutationError ? (
          <Alert variant="destructive" role="alert">
            <AlertTitle>A ação não foi concluída</AlertTitle>
            <AlertDescription>{describeError(mutationError)}</AlertDescription>
          </Alert>
        ) : null}
      </CardContent>
    </Card>
  );
}
