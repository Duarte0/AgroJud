import type { RuralLink, TriageDecision } from "@/api/types";
import { Badge } from "@/components/ui/badge";
export function ProcessState({ decision, ruralLink }: { decision: TriageDecision; ruralLink: RuralLink }) {
  return <div className="flex flex-wrap items-center gap-2">
    <Badge variant={decision === "pending" ? "warning" : decision === "relevant" ? "info" : "secondary"}>{ {pending: "Pendente", relevant: "Relevante", discarded: "Descartado"}[decision] }</Badge>
    <span className="text-xs text-muted-foreground">{ruralLink === "confirmed" ? "Vínculo confirmado" : "Vínculo não confirmado"}</span>
  </div>;
}
