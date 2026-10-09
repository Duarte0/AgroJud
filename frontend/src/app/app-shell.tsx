import { Database, FlaskConical, Radar } from "lucide-react";
import { NavLink, Outlet } from "react-router";

import { useEnvironmentQuery } from "@/api/queries";
import type { Environment } from "@/api/types";
import { EnvironmentContext } from "@/app/environment-context";
import { ErrorState, LoadingState } from "@/components/query-feedback";
import { environmentLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/radar", label: "Radar" },
  { to: "/jobs", label: "Coletas" },
  { to: "/processes", label: "Processos" },
] as const;

function EnvironmentBar({ environment }: { environment: Environment }) {
  const isDemo = environment.environment !== "real";
  const Icon = isDemo ? FlaskConical : Database;
  return (
    <div
      role="region"
      aria-label="Ambiente"
      data-environment={environment.environment}
      className={cn(
        "border-b px-4 py-1.5 text-sm",
        isDemo ? "bg-warning text-warning-foreground" : "bg-info text-info-foreground",
      )}
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-3 gap-y-1">
        <span className="flex items-center gap-1.5 font-semibold">
          <Icon className="size-4" aria-hidden="true" />
          Ambiente: {environmentLabel(environment.environment)}
        </span>
        <span>
          {isDemo
            ? "Os resultados são fixtures sintéticas e não representam o TJGO."
            : environment.source_enabled
              ? "Fonte DataJud habilitada."
              : `Coleta indisponível: ${environment.source_disabled_reason ?? "fonte desabilitada."}`}
        </span>
      </div>
    </div>
  );
}

function Header() {
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3">
        <NavLink to="/radar" className="flex items-center gap-2 rounded-md font-semibold">
          <Radar className="size-5 text-primary" aria-hidden="true" />
          AgroJud Radar
        </NavLink>
        <nav aria-label="Principal">
          <ul className="flex gap-1">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      "block rounded-md px-3 py-1.5 text-sm font-medium transition-colors hover:bg-accent",
                      isActive ? "bg-secondary text-foreground" : "text-muted-foreground",
                    )
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}

export function AppShell() {
  const environment = useEnvironmentQuery();

  return (
    <div className="flex min-h-svh flex-col">
      <a
        href="#conteudo"
        className="sr-only z-50 rounded-md bg-card px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Pular para o conteúdo
      </a>
      <div className="sticky top-0 z-40">
        {environment.data ? <EnvironmentBar environment={environment.data} /> : null}
        <Header />
      </div>
      <main id="conteudo" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        {environment.data ? (
          <EnvironmentContext.Provider value={environment.data}>
            <Outlet />
          </EnvironmentContext.Provider>
        ) : environment.isError ? (
          <ErrorState
            title="Não foi possível identificar o ambiente da API"
            error={environment.error}
            onRetry={() => void environment.refetch()}
            retrying={environment.isFetching}
          />
        ) : (
          <LoadingState label="Identificando o ambiente…" />
        )}
      </main>
    </div>
  );
}
