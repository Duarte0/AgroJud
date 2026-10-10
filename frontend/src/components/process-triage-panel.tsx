import { useCallback, useState, type SubmitEvent } from "react";
import { useBeforeUnload, useBlocker, useSearchParams } from "react-router";

import { ApiError, describeError } from "@/api/client";
import {
  useProcess,
  useProcessTriageHistory,
  useUpdateProcessTriage,
} from "@/api/queries";
import type {
  ProcessTriage,
  ProcessTriageHistoryEntry,
  ProcessTriagePatch,
} from "@/api/types";
import { ConfirmAction } from "@/components/confirm-action";
import { Pagination } from "@/components/pagination";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { ErrorState, LoadingState, StaleNotice } from "@/components/query-feedback";
import { formatDateTime } from "@/lib/format";
import { readPage, withPage } from "@/lib/search-params";

const HISTORY_PAGE_SIZE = 20;

function decisionLabel(value: ProcessTriage["decision"]): string {
  return { pending: "Pendente", relevant: "Relevante", discarded: "Descartado" }[value];
}

function ruralLinkLabel(value: ProcessTriage["rural_link"]): string {
  return { unconfirmed: "Não confirmado", confirmed: "Confirmado manualmente" }[value];
}

function snapshotText(entry: ProcessTriageHistoryEntry, side: "previous_state" | "new_state") {
  const state = entry[side];
  return (
    <div className="space-y-1 text-sm">
      <p>
        <span className="font-medium">Decisão:</span> {decisionLabel(state.decision)}
      </p>
      <p>
        <span className="font-medium">Vínculo rural:</span> {ruralLinkLabel(state.rural_link)}
      </p>
      <p>
        <span className="font-medium">Nota:</span>
      </p>
      <p className="break-words whitespace-pre-wrap text-muted-foreground">
        {state.note || "Sem nota"}
      </p>
    </div>
  );
}

function HistoryEntry({ entry }: { entry: ProcessTriageHistoryEntry }) {
  return (
    <li className="rounded-lg border p-4" data-testid="triage-history-entry">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-medium">Versão {entry.version} · origem manual</h3>
        <time className="text-sm text-muted-foreground" dateTime={entry.created_at}>
          {formatDateTime(entry.created_at)}
        </time>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <section aria-label={`Estado anterior à versão ${entry.version}`}>
          <h4 className="mb-1 text-sm font-medium">Antes</h4>
          {snapshotText(entry, "previous_state")}
        </section>
        <section aria-label={`Estado posterior à versão ${entry.version}`}>
          <h4 className="mb-1 text-sm font-medium">Depois</h4>
          {snapshotText(entry, "new_state")}
        </section>
      </div>
    </li>
  );
}

export function ProcessTriagePanel({
  processId,
  initialTriage,
}: {
  processId: string;
  initialTriage: ProcessTriage;
}) {
  const [searchParams] = useSearchParams();
  const historyPage = readPage(searchParams, "triage_page");
  const process = useProcess(processId);
  const serverTriage = process.data?.triage ?? initialTriage;
  const history = useProcessTriageHistory(processId, historyPage);
  const update = useUpdateProcessTriage(processId);
  const [draftState, setDraftState] = useState({ dirty: false, value: initialTriage });
  const draft = draftState.dirty ? draftState.value : serverTriage;
  const [conflict, setConflict] = useState(false);
  const [rebased, setRebased] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const saveConflict = update.error instanceof ApiError && update.error.status === 409;

  const hasChanges =
    draft.decision !== serverTriage.decision ||
    draft.rural_link !== serverTriage.rural_link ||
    draft.note !== serverTriage.note;

  const blocker = useBlocker(({ currentLocation, nextLocation }) => hasChanges && currentLocation.pathname !== nextLocation.pathname);
  useBeforeUnload(useCallback((event: BeforeUnloadEvent) => {
    if (hasChanges) { event.preventDefault(); event.returnValue = ""; }
  }, [hasChanges]));

  function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setValidationError(null);
    if (draft.rural_link === "confirmed" && !draft.note.trim()) {
      setValidationError("Informe uma nota não vazia para confirmar o vínculo rural.");
      document.getElementById("triage-note")?.focus();
      return;
    }

    const body: ProcessTriagePatch = { expected_version: serverTriage.version };
    if (draft.decision !== serverTriage.decision) body.decision = draft.decision;
    if (draft.rural_link !== serverTriage.rural_link) body.rural_link = draft.rural_link;
    if (draft.note !== serverTriage.note) body.note = draft.note;
    update.mutate(body, {
      onSuccess: (saved) => {
        setDraftState({ value: saved, dirty: false });
        setConflict(false);
        setRebased(false);
      },
      onError: (error) => {
        if (error instanceof ApiError && error.status === 409) {
          setConflict(true);
          setRebased(false);
        }
      },
    });
  }

  async function reloadAfterConflict() {
    setReloading(true);
    setReloadError(null);
    try {
      const refreshed = await process.refetch();
      await history.refetch();
      if (refreshed.isError || !refreshed.data) {
        setReloadError(
          refreshed.error ? describeError(refreshed.error) : "Não foi possível recarregar os dados.",
        );
        return;
      }
      setConflict(false);
      setRebased(true);
    } finally {
      setReloading(false);
    }
  }

  function changeDraft(next: Partial<ProcessTriage>) {
    setDraftState((current) => {
      const base = current.dirty ? current.value : serverTriage;
      const value = { ...base, ...next };
      const dirty =
        value.decision !== serverTriage.decision ||
        value.rural_link !== serverTriage.rural_link ||
        value.note !== serverTriage.note;
      return { value, dirty };
    });
    setRebased(false);
  }

  return (
    <section className="space-y-4" aria-labelledby="triage-title">
      <ConfirmAction open={blocker.state === "blocked"} onOpenChange={open => { if (!open && blocker.state === "blocked") blocker.reset(); }} title="Sair sem salvar a triagem?" description="A nota e as alterações deste rascunho ainda não foram salvas." confirmLabel="Sair sem salvar" onConfirm={() => { if (blocker.state === "blocked") blocker.proceed(); }} />
      <Card>
        <CardHeader>
          <CardTitle id="triage-title">Triagem humana</CardTitle>
          <CardDescription>
            Decisão e vínculo rural são avaliações independentes. Marcar como relevante não inicia
            acompanhamento.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Versão {serverTriage.version}
            {serverTriage.updated_at ? ` · atualizada em ${formatDateTime(serverTriage.updated_at)}` : " · ainda não revisada"}
          </p>

          {conflict ? (
            <Alert variant="warning" role="alert">
              <AlertTitle>Esta triagem mudou em outra edição</AlertTitle>
              <AlertDescription>
                Recarregue o estado salvo para atualizar a versão. Seus campos digitados serão
                mantidos para revisão antes de salvar novamente.
                {reloadError ? <p role="status">{reloadError}</p> : null}
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  disabled={reloading}
                  onClick={() => void reloadAfterConflict()}
                >
                  {reloading ? "Recarregando…" : "Recarregar dados"}
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
          {rebased ? (
            <Alert variant="info" role="status">
              <AlertTitle>Dados atualizados</AlertTitle>
              <AlertDescription>
                O rascunho foi mantido. Confira a versão {serverTriage.version} e salve novamente.
              </AlertDescription>
            </Alert>
          ) : null}
          {validationError ? (
            <Alert variant="destructive" role="alert">
              <AlertTitle>Nota necessária</AlertTitle>
              <AlertDescription>Revise o campo indicado abaixo.</AlertDescription>
            </Alert>
          ) : null}
          {update.isError && !conflict && !saveConflict ? (
            <Alert variant="destructive" role="alert">
              <AlertTitle>Não foi possível salvar a triagem</AlertTitle>
              <AlertDescription>{describeError(update.error)}</AlertDescription>
            </Alert>
          ) : null}

          <form aria-label="Editar triagem" onSubmit={submit} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="triage-decision">Decisão</Label>
                <NativeSelect
                  id="triage-decision"
                  disabled={update.isPending}
                  value={draft.decision}
                  onChange={(event) =>
                    changeDraft({ decision: event.target.value as ProcessTriage["decision"] })
                  }
                >
                  <option value="pending">Pendente</option>
                  <option value="relevant">Relevante</option>
                  <option value="discarded">Descartado</option>
                </NativeSelect>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="triage-rural-link">Vínculo rural</Label>
                <NativeSelect
                  id="triage-rural-link"
                  disabled={update.isPending}
                  value={draft.rural_link}
                  onChange={(event) =>
                    changeDraft({ rural_link: event.target.value as ProcessTriage["rural_link"] })
                  }
                >
                  <option value="unconfirmed">Não confirmado</option>
                  <option value="confirmed">Confirmado manualmente</option>
                </NativeSelect>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="triage-note">Nota de revisão</Label>
              <textarea
                id="triage-note"
                value={draft.note}
                maxLength={5000}
                rows={4}
                aria-describedby="triage-note-help triage-note-count triage-note-error"
                aria-invalid={Boolean(validationError)}
                name="triage-note"
                disabled={update.isPending}
                className="flex min-h-24 w-full rounded-md border border-input bg-card px-3 py-2 text-base shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 md:text-sm"
                onChange={(event) => changeDraft({ note: event.target.value })}
              />
              <div className="flex flex-wrap justify-between gap-2 text-xs text-muted-foreground">
                <span id="triage-note-help">Texto simples, sem formatação ativa.</span>
                <span id="triage-note-count">{draft.note.length}/5000 caracteres</span>
              </div>
            </div>
            <p id="triage-note-error" className="text-sm text-destructive">{validationError}</p>
            <p role="status" className="text-xs text-muted-foreground">{hasChanges ? "Alterações não salvas" : update.isSuccess ? "Triagem salva." : ""}</p>
            <Button type="submit" disabled={update.isPending || !hasChanges}>
              {update.isPending ? "Salvando…" : "Salvar triagem"}
            </Button>
          </form>
        </CardContent>
      </Card>


    </section>
  );
}

export function ProcessTriageHistory({ processId }: { processId: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const historyPage = readPage(searchParams, "triage_page");
  const history = useProcessTriageHistory(processId, historyPage);
  return (      <Card>
        <CardHeader>
          <CardTitle>Histórico de triagem</CardTitle>
          <CardDescription>
            Cada entrada registra o estado anterior e posterior, a versão, a data e a origem manual.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {history.isPending ? (
            <LoadingState label="Carregando histórico…" />
          ) : !history.data ? (
            <ErrorState
              title="Não foi possível carregar o histórico"
              error={history.error}
              onRetry={() => void history.refetch()}
              retrying={history.isFetching}
            />
          ) : (
            <div className="space-y-4" aria-busy={history.isPlaceholderData}>
              <StaleNotice query={history} />
              {history.data.items.length === 0 ? (
                <p className="text-sm text-muted-foreground">Nenhuma alteração humana registrada.</p>
              ) : (
                <ol className="space-y-3">
                  {history.data.items.map((entry) => (
                    <HistoryEntry key={entry.id} entry={entry} />
                  ))}
                </ol>
              )}
              {history.data.total > HISTORY_PAGE_SIZE ? (
                <Pagination
                  label="Paginação do histórico de triagem"
                  page={historyPage}
                  pageSize={HISTORY_PAGE_SIZE}
                  total={history.data.total}
                  disabled={history.isPlaceholderData}
                  onPageChange={(next) =>
                    setSearchParams(withPage(searchParams, next, "triage_page"))
                  }
                />
              ) : null}
            </div>
          )}
        </CardContent>
      </Card>);
}
