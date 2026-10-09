import type { JobCommand, JobDetail } from "@/api/types";
import { isActiveJob } from "@/lib/job-progress";

export type JobAction = {
  command: JobCommand;
  label: string;
  pendingLabel: string;
  description: string;
  variant: "default" | "outline" | "destructive";
};

const ACTIONS: Record<JobCommand, JobAction> = {
  cancel: {
    command: "cancel",
    label: "Cancelar coleta",
    pendingLabel: "Cancelando…",
    description:
      "Interrompe a coleta. Resultados de páginas já confirmadas permanecem na base local.",
    variant: "destructive",
  },
  resume: {
    command: "resume",
    label: "Retomar coleta",
    pendingLabel: "Retomando…",
    description: "Retoma a partir do último checkpoint confirmado.",
    variant: "default",
  },
  continue: {
    command: "continue",
    label: "Continuar coleta",
    pendingLabel: "Continuando…",
    description: "Libera um novo orçamento de registros a partir do checkpoint atual.",
    variant: "default",
  },
  "restart-scan": {
    command: "restart-scan",
    label: "Reiniciar varredura",
    pendingLabel: "Reiniciando…",
    description: "A fonte invalidou o cursor; inicia uma nova varredura vinculada a esta.",
    variant: "outline",
  },
};

/** Commands that the persisted state may accept; the API remains the authority (409). */
export function availableJobActions(job: JobDetail): JobAction[] {
  const actions: JobAction[] = [];
  if (isActiveJob(job.status) && !job.cancel_requested) actions.push(ACTIONS.cancel);
  if (job.cursor_invalid) {
    actions.push(ACTIONS["restart-scan"]);
  } else if (job.status === "failed" || job.status === "cancelled") {
    actions.push(ACTIONS.resume);
  }
  if (job.status === "partial" && job.reason === "limit") actions.push(ACTIONS.continue);
  return actions;
}
