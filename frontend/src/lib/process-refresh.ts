import type { ProcessRefreshResult } from "@/api/types";

export function refreshResultLabel(result: ProcessRefreshResult): string {
  switch (result.state) {
    case "pending":
      return result.job_status === "retry_wait"
        ? "Aguardando nova tentativa"
        : result.job_status === "queued"
          ? "Atualização na fila"
          : "Atualização em andamento";
    case "found":
      return "Encontrado na consulta";
    case "absent_in_query":
      return "Ausente nesta consulta";
    case "partial":
      return "Consulta parcial";
    case "failed":
      return "Falha na consulta";
    case "cancelled":
      return "Consulta cancelada";
  }
}
