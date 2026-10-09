import { AlertTriangle, Inbox, Loader2, RefreshCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";

import { describeError, NetworkError } from "@/api/client";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime } from "@/lib/format";

export function LoadingState({ label }: { label: string }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-3 py-2">
      <span className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden="true" />
        {label}
      </span>
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-3/4" />
    </div>
  );
}

type RetryProps = {
  onRetry: () => void;
  retrying: boolean;
};

function RetryButton({ onRetry, retrying }: RetryProps) {
  return (
    <Button variant="outline" size="sm" onClick={onRetry} disabled={retrying}>
      <RefreshCw className={retrying ? "animate-spin" : undefined} aria-hidden="true" />
      {retrying ? "Tentando novamente…" : "Tentar novamente"}
    </Button>
  );
}

/** Reading failed and no data is known: this is never presented as an empty list. */
export function ErrorState({
  title,
  error,
  ...retry
}: RetryProps & { title: string; error: unknown }) {
  const Icon = error instanceof NetworkError ? WifiOff : AlertTriangle;
  return (
    <Alert variant="destructive" role="alert" data-state="error">
      <Icon aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{describeError(error)}</p>
        <p className="text-muted-foreground">
          Tentar novamente apenas repete a leitura; nenhuma coleta é criada.
        </p>
        <RetryButton {...retry} />
      </AlertDescription>
    </Alert>
  );
}

/** A refresh failed while older cached data is still shown. */
export function StaleDataBanner({
  error,
  updatedAt,
  ...retry
}: RetryProps & { error: unknown; updatedAt: number }) {
  return (
    <Alert variant="warning" role="alert" data-state="stale">
      <AlertTriangle aria-hidden="true" />
      <AlertTitle>Falha ao atualizar — exibindo dados possivelmente desatualizados</AlertTitle>
      <AlertDescription>
        <p>
          {describeError(error)} Dados obtidos em{" "}
          {formatDateTime(new Date(updatedAt).toISOString())}.
        </p>
        <RetryButton {...retry} />
      </AlertDescription>
    </Alert>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div
      data-state="empty"
      className="flex flex-col items-center gap-2 rounded-lg border border-dashed px-4 py-10 text-center"
    >
      <Inbox className="size-6 text-muted-foreground" aria-hidden="true" />
      <p className="font-medium">{title}</p>
      {children ? <div className="max-w-prose text-sm text-muted-foreground">{children}</div> : null}
    </div>
  );
}

type ReadQuery = {
  error: unknown;
  isError: boolean;
  isFetching: boolean;
  dataUpdatedAt: number;
  refetch: () => unknown;
};

/** Shows the stale banner only when cached data survives a failed refresh. */
export function StaleNotice({ query }: { query: ReadQuery }) {
  if (!query.isError) return null;
  return (
    <StaleDataBanner
      error={query.error}
      updatedAt={query.dataUpdatedAt}
      onRetry={() => void query.refetch()}
      retrying={query.isFetching}
    />
  );
}
