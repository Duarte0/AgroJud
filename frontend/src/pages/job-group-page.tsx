import { useState } from "react";
import { Link, useParams } from "react-router";

import { describeError } from "@/api/client";
import { useCancelJobGroup, useJobGroup } from "@/api/queries";
import type { ExecutionGroup, ExecutionGroupMember } from "@/api/types";
import { ConfirmAction } from "@/components/confirm-action";
import { PageHeader } from "@/components/page-header";
import { usePageTitle } from "@/hooks/use-page-title";
import { ErrorState, LoadingState, StaleNotice } from "@/components/query-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DescriptionList } from "@/components/description-list";
import { formatCount, formatDateTime, jobKindLabel } from "@/lib/format";
import { readJobProgress } from "@/lib/job-progress";

const GROUP_STATE_LABELS: Record<ExecutionGroup["state"], string> = {
  queued: "Na fila",
  running: "Em andamento",
  completed: "Concluída",
  partial: "Concluída parcialmente",
  failed: "Falhou",
  blocked: "Bloqueada",
  cancelled: "Cancelada",
};

const MEMBER_STATE_LABELS: Record<ExecutionGroupMember["state"], string> = {
  QUEUED: "Na fila",
  RUNNING: "Em andamento",
  RETRY_WAIT: "Aguardando nova tentativa",
  COMPLETED: "Concluída",
  PARTIAL: "Parcial",
  FAILED: "Falhou",
  CANCELLED: "Cancelada",
  BLOCKED_NOT_VALIDATED: "Bloqueada: capacidade não validada",
  NOT_RUN: "Não executada",
};

function MemberCard({ member }: { member: ExecutionGroupMember }) {
  const progress = readJobProgress(member.coverage);
  const tone = member.state === "FAILED" || member.state === "BLOCKED_NOT_VALIDATED"
    ? "destructive"
    : member.state === "PARTIAL" || member.state === "RETRY_WAIT"
      ? "warning"
      : member.state === "COMPLETED"
        ? "success"
        : "secondary";
  return (
    <li>
      <Card data-testid="job-group-member" data-tribunal={member.tribunal}>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>{member.tribunal}</CardTitle>
              <CardDescription>{member.availability_state}</CardDescription>
            </div>
            <Badge variant={tone}>{MEMBER_STATE_LABELS[member.state]}</Badge>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <DescriptionList
            items={[
              { term: "Capas confirmadas", value: formatCount(progress.hitsConfirmed) },
              { term: "Páginas confirmadas", value: formatCount(progress.pagesConfirmed) },
              { term: "Válidos / rejeitados", value: `${formatCount(progress.validHits)} / ${formatCount(progress.rejectedHits)}` },
              { term: "Novos / atualizados / sem alteração", value: `${formatCount(progress.newCount)} / ${formatCount(progress.updatedCount)} / ${formatCount(progress.unchangedCount)}` },
              { term: "Próxima página", value: member.next_page === null ? "Ainda sem checkpoint" : formatCount(member.next_page) },
              { term: "Revisão do checkpoint", value: member.checkpoint_revision === null ? "—" : formatCount(member.checkpoint_revision) },
              { term: "Checkpoint atualizado", value: formatDateTime(member.checkpoint_updated_at) },
              { term: "Job filho", value: member.job_id ? <Link to={`/jobs/${member.job_id}`} className="text-primary underline-offset-4 hover:underline">Abrir execução</Link> : "Nenhum job criado" },
              { term: "Coleta", value: member.collection_id ? <Link to={`/jobs/${member.job_id}`} className="text-primary underline-offset-4 hover:underline">{member.collection_id}</Link> : "—" },
            ]}
          />
          {member.reason ? (
            <p className="rounded-md bg-muted/60 p-3 text-sm text-warning-foreground" role="status">
              {member.reason}
            </p>
          ) : null}
        </CardContent>
      </Card>
    </li>
  );
}

export function JobGroupPage() {
  usePageTitle("Execução por tribunal");
  const { groupId = "" } = useParams();
  const group = useJobGroup(groupId);
  const cancel = useCancelJobGroup(groupId);
  const [cancelOpen, setCancelOpen] = useState(false);
  const mayCancel = group.data?.state === "queued" || group.data?.state === "running";

  return (
    <>
      <PageHeader
        title="Execução por tribunal"
        description="Cada tribunal mantém seu próprio job, cobertura e checkpoint."
      />
      {group.isPending ? (
        <LoadingState label="Carregando o grupo de execução…" variant="detail" />
      ) : !group.data ? (
        <ErrorState
          title="Não foi possível carregar o grupo"
          error={group.error}
          onRetry={() => void group.refetch()}
          retrying={group.isFetching}
        />
      ) : (
        <div className="space-y-5" aria-busy={group.isFetching}>
          <StaleNotice query={group} />
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="space-y-1">
                  <CardTitle>{jobKindLabel(group.data.kind)} · {GROUP_STATE_LABELS[group.data.state]}</CardTitle>
                  <CardDescription>
                    {formatCount(group.data.completed_count)} de {formatCount(group.data.member_count)} tribunais concluídos
                    {group.data.finished_at ? ` · finalizado em ${formatDateTime(group.data.finished_at)}` : ""}
                  </CardDescription>
                </div>
                {mayCancel ? (
                  <ConfirmAction
                    open={cancelOpen}
                    onOpenChange={setCancelOpen}
                    title="Cancelar os tribunais ainda ativos?"
                    description="Jobs em execução receberão pedido de parada cooperativa. Páginas e checkpoints já confirmados serão preservados."
                    confirmLabel={cancel.isPending ? "Cancelando…" : "Cancelar grupo"}
                    onConfirm={() => cancel.mutate(undefined, { onSuccess: () => setCancelOpen(false) })}
                  >
                    <Button variant="destructive" disabled={cancel.isPending}>Cancelar grupo</Button>
                  </ConfirmAction>
                ) : null}
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {group.data.member_count > 0 ? (
                <div className="space-y-1">
                  <div className="flex justify-between text-sm">
                    <span>Tribunais concluídos</span>
                    <span>{Math.floor(group.data.completed_count / group.data.member_count * 100)}%</span>
                  </div>
                  <div
                    role="progressbar"
                    aria-label="Tribunais concluídos"
                    aria-valuemin={0}
                    aria-valuemax={group.data.member_count}
                    aria-valuenow={group.data.completed_count}
                    className="h-2 overflow-hidden rounded-full bg-muted"
                  >
                    <div
                      className="h-full bg-primary"
                      style={{ width: `${group.data.completed_count / group.data.member_count * 100}%` }}
                    />
                  </div>
                </div>
              ) : null}
              <dl className="grid gap-3 text-sm sm:grid-cols-2">
                <div><dt className="text-xs text-muted-foreground">Criado em</dt><dd>{formatDateTime(group.data.created_at)}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Matriz revisada</dt><dd className="break-all">{group.data.matrix_sha256 ?? "Não registrada"}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Allowlist</dt><dd>{group.data.allowlist_version ?? "—"}</dd></div>
                <div><dt className="text-xs text-muted-foreground">Catálogo / TPU</dt><dd>{group.data.catalog_version ?? "—"} / {group.data.tpu_version ?? "—"}</dd></div>
              </dl>
            </CardContent>
          </Card>
          {group.data.state === "partial" ? (
            <Alert variant="warning" data-state="partial">
              <AlertTitle>Resultado concluído parcialmente</AlertTitle>
              <AlertDescription>
                Os resultados dos tribunais concluídos continuam disponíveis. Revise o motivo e o checkpoint de cada membro.
              </AlertDescription>
            </Alert>
          ) : null}
          {cancel.error ? (
            <Alert variant="destructive" role="alert">
              <AlertTitle>O cancelamento do grupo não foi concluído</AlertTitle>
              <AlertDescription>{describeError(cancel.error)}</AlertDescription>
            </Alert>
          ) : null}
          <section className="space-y-3" aria-labelledby="group-members-title">
            <div>
              <h2 id="group-members-title" className="text-lg font-semibold">Resultado por tribunal</h2>
              <p className="text-sm text-muted-foreground">A cobertura de cada membro vem do próprio job e não é somada entre tribunais.</p>
            </div>
            {group.data.members.length ? (
              <ul className="grid gap-4 lg:grid-cols-2">
                {group.data.members.map((member) => <MemberCard key={member.id} member={member} />)}
              </ul>
            ) : (
              <p className="rounded-md border p-4 text-sm text-warning-foreground" role="status">
                Nenhuma representação local permitiu selecionar um tribunal para esta atualização. Nenhuma consulta foi enviada.
              </p>
            )}
          </section>
        </div>
      )}
    </>
  );
}
