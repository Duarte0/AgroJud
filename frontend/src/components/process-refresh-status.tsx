import { Link } from "react-router";

import type { ProcessRefreshResult } from "@/api/types";
import { formatDateTime, jobStatusLabel } from "@/lib/format";
import { refreshResultLabel } from "@/lib/process-refresh";

export function ProcessRefreshStatus({
  result,
}: {
  result: ProcessRefreshResult | null;
}) {
  if (!result) {
    return <p className="text-sm text-muted-foreground">Nenhuma atualização manual registrada.</p>;
  }

  return (
    <p className="text-sm" data-testid="refresh-result">
      <span className="font-medium">{refreshResultLabel(result)}</span>
      {result.hit_count !== null ? ` · ${result.hit_count} capa(s) retornada(s)` : ""}
      {" · "}
      {formatDateTime(result.checked_at)}
      {" · "}
      <Link
        to={`/jobs/${result.job_id}`}
        className="text-primary underline-offset-4 hover:underline"
      >
        Ver atualização ({jobStatusLabel(result.job_status).toLowerCase()})
      </Link>
    </p>
  );
}
