import { useState } from "react";
import { Link, useLocation } from "react-router";

import { describeError } from "@/api/client";
import {
  useCreateSignalRun,
  useProcessSignals,
  useResumeSignalRun,
  useSignalRules,
} from "@/api/queries";
import type { ProcessSignal, SignalRule, SignalRules, SignalRunStatus } from "@/api/types";
import { EmptyState, ErrorState, LoadingState, StaleNotice } from "@/components/query-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatDateTime } from "@/lib/format";

const categories: Record<string, string> = {
  penhora: "Penhora, bloqueio ou arresto",
  leilao: "Leilão",
  recuperacao_judicial: "Recuperação judicial",
};

function runStatusLabel(run: SignalRunStatus): string {
  switch (run.status) {
    case "queued":
      return "Na fila";
    case "running":
      return "Em execução";
    case "retry_wait":
      return "Aguardando retomada automática";
    case "completed":
      return "Concluído";
    case "partial":
      return "Parcial";
    case "failed":
      return "Falhou";
    case "cancelled":
      return "Cancelado";
  }
}

function SignalRunStatusCard({
  run,
  onResume,
  resuming,
}: {
  run: SignalRunStatus;
  onResume: () => void;
  resuming: boolean;
}) {
  const active = run.status === "queued" || run.status === "running" || run.status === "retry_wait";
  const message = active
    ? `${run.processed_input_count} de ${run.input_count} entradas processadas · ${run.completed_process_count} processo(s) publicado(s)`
    : run.status === "completed" && run.not_evaluated_process_count > 0
      ? `${run.not_evaluated_process_count} processo(s) mantiveram o resultado anterior por falta de evidência completa.`
      : run.status === "completed" && run.stale_process_count > 0
        ? `${run.stale_process_count} processo(s) foram avaliados com evidência defasada.`
        : run.reason ?? "";

  return (
    <Alert variant={run.status === "failed" ? "destructive" : "info"}>
      <AlertTitle>Último reprocessamento: {runStatusLabel(run)}</AlertTitle>
      <AlertDescription>
        {message ? <p>{message}</p> : null}
        {run.resumable ? (
          <Button className="mt-2" size="sm" variant="outline" onClick={onResume} disabled={resuming}>
            {resuming ? "Retomando…" : "Retomar execução"}
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}

function SignalCard({ processId, signal }: { processId: string; signal: ProcessSignal }) {
  const location = useLocation();
  const evidenceHref = signal.movement_occurrence_id
    ? `/processes/${processId}?evidence=${signal.movement_occurrence_id}#timeline`
    : null;
  return (
    <li className="rounded-lg border bg-card p-4" data-testid="process-signal">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={signal.state === "current" ? "info" : "secondary"}>
          {signal.state === "current" ? "Vigente" : "Histórico"}
        </Badge>
        <Badge variant="outline">{categories[signal.category] ?? signal.category}</Badge>
        {signal.evidence_stale ? <Badge variant="warning">Evidência defasada</Badge> : null}
      </div>
      <h3 className="mt-2 font-medium">{signal.rule_name}</h3>
      <p className="text-sm text-muted-foreground">
        Regra {signal.rule_id} · versão {signal.rule_version} · execução {signal.environment === "demo" ? "demonstrativa" : "real"}
      </p>
      <p className="mt-2 text-sm">{signal.explanation}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Avaliado em {formatDateTime(signal.evaluated_at)} · {signal.rule_enablement.reason}
      </p>
      {evidenceHref ? (
        <Link
          to={evidenceHref}
          state={location.state}
          className="mt-2 inline-block text-sm text-primary underline-offset-4 hover:underline"
        >
          Ver evidência na linha do tempo
        </Link>
      ) : null}
    </li>
  );
}

export function ProcessSignalsPanel({ processId }: { processId: string }) {
  const signals = useProcessSignals(processId);
  const rules = useSignalRules();
  const createRun = useCreateSignalRun(processId);
  const resumeRun = useResumeSignalRun(processId);
  const [ruleSelection, setRuleSelection] = useState<{
    environment: SignalRules["environment"];
    ruleIds: string[];
  } | null>(null);
  const latestRun = signals.data?.latest_run;
  const enabledRules = rules.data?.items.filter((rule) => rule.enablement.enabled) ?? [];
  const enabledRuleIds = enabledRules.map((rule) => rule.id);
  const selectedRuleIds =
    rules.data && ruleSelection?.environment === rules.data.environment
      ? ruleSelection.ruleIds.filter((ruleId) => enabledRuleIds.includes(ruleId))
      : enabledRuleIds;
  const selectedRuleIdSet = new Set(selectedRuleIds);
  const active =
    latestRun?.status === "queued" ||
    latestRun?.status === "running" ||
    latestRun?.status === "retry_wait";

  if (signals.isPending) return <LoadingState label="Carregando sinais…" />;
  if (!signals.data) {
    return (
      <ErrorState
        title="Não foi possível carregar os sinais"
        error={signals.error}
        onRetry={() => void signals.refetch()}
        retrying={signals.isFetching}
      />
    );
  }

  function updateRuleSelection(rule: SignalRule, checked: boolean) {
    if (!rules.data || !rule.enablement.enabled) return;
    const ruleIds = checked
      ? [...new Set([...selectedRuleIds, rule.id])]
      : selectedRuleIds.filter((ruleId) => ruleId !== rule.id);
    setRuleSelection({ environment: rules.data.environment, ruleIds });
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sinais estruturados</CardTitle>
        <CardDescription>
          Regras determinísticas sobre ocorrências normalizadas. Um sinal descreve evidência para revisão e não confirma vínculo rural.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <StaleNotice query={signals} />
        <fieldset className="space-y-3 rounded-lg border p-4">
          <legend className="px-1 text-sm font-medium">Regras para este ambiente</legend>
          <p className="text-sm text-muted-foreground">
            As regras habilitadas começam selecionadas. Regras indisponíveis aparecem com o motivo e não podem ser executadas.
          </p>
          {rules.isPending ? (
            <LoadingState label="Carregando regras…" variant="detail" />
          ) : !rules.data ? (
            <ErrorState
              title="Não foi possível carregar as regras de sinais"
              error={rules.error}
              onRetry={() => void rules.refetch()}
              retrying={rules.isFetching}
            />
          ) : (
            <ul className="space-y-3">
              {rules.data.items.map((rule) => (
                <li key={rule.id} className="rounded-md border p-3">
                  <label className="flex items-start gap-3">
                    <input
                      className="mt-1 size-4 accent-primary"
                      type="checkbox"
                      checked={selectedRuleIdSet.has(rule.id)}
                      disabled={!rule.enablement.enabled}
                      onChange={(event) => updateRuleSelection(rule, event.currentTarget.checked)}
                    />
                    <span className="min-w-0">
                      <span className="block font-medium">{rule.name}</span>
                      <span className="block text-sm text-muted-foreground">
                        {categories[rule.category] ?? rule.category} · regra {rule.id} · versão {rule.version}
                      </span>
                      <span className="block text-sm text-muted-foreground">
                        {rule.enablement.enabled ? "Habilitada" : "Indisponível"}: {rule.enablement.reason}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {rules.data && selectedRuleIds.length === 0 ? (
            <p role="status" className="text-sm text-muted-foreground">
              Selecione ao menos uma regra habilitada para reprocessar.
            </p>
          ) : null}
        </fieldset>
        {latestRun ? (
          <SignalRunStatusCard
            run={latestRun}
            onResume={() => resumeRun.mutate(latestRun.job_id)}
            resuming={resumeRun.isPending}
          />
        ) : null}
        {createRun.error ? <p role="alert" className="text-sm text-destructive">{describeError(createRun.error)}</p> : null}
        {resumeRun.error ? <p role="alert" className="text-sm text-destructive">{describeError(resumeRun.error)}</p> : null}
        <Button
          onClick={() => createRun.mutate(selectedRuleIds)}
          disabled={createRun.isPending || active || !rules.data || selectedRuleIds.length === 0}
        >
          {createRun.isPending ? "Enfileirando…" : "Reprocessar sinais deste processo"}
        </Button>
        {signals.data.items.length === 0 ? (
          <EmptyState title="Nenhum sinal vigente ou histórico">
            O reprocessamento usa somente versões locais completamente normalizadas.
          </EmptyState>
        ) : (
          <ScrollArea label="Lista de sinais vigentes e históricos">
            <ul className="space-y-3">
              {signals.data.items.map((signal) => (
                <SignalCard key={signal.id} processId={processId} signal={signal} />
              ))}
            </ul>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  );
}
