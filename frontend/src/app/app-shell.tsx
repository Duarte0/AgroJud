import { Database, FlaskConical, Radar, LayoutDashboard, Files, Bookmark, Bell, Layers, Menu, X } from "lucide-react";
import { Dialog } from "radix-ui";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";
import { useEnvironmentQuery } from "@/api/queries";
import type { Environment } from "@/api/types";
import { EnvironmentContext } from "@/app/environment-context";
import { ErrorState, LoadingState } from "@/components/query-feedback";
import { Button } from "@/components/ui/button";
import { environmentLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

const GROUPS = [
  { label: "Trabalho", items: [{to: "/", label: "Visão geral", icon: LayoutDashboard}, {to: "/processes", label: "Processos", icon: Files}, {to: "/watchlist", label: "Acompanhados", icon: Bookmark}, {to: "/news", label: "Novidades", icon: Bell}] },
  { label: "Coleta", items: [{to: "/radar", label: "Radar", icon: Radar}, {to: "/jobs", label: "Coletas", icon: Layers}] },
];
function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return <nav aria-label="Principal">{GROUPS.map(group => <div key={group.label}>
    <p className="nav-group-label">{group.label}</p>
    <ul className="flex flex-col gap-1">{group.items.map(({to, label, icon: Icon}) => <li key={to}>
      <NavLink to={to} end={to === "/"} className="nav-link" title={label} aria-label={label} onClick={onNavigate}>
        <Icon className="size-5 shrink-0" strokeWidth={1.7} aria-hidden="true" /><span className="nav-label">{label}</span>
      </NavLink>
    </li>)}</ul>
  </div>)}</nav>;
}
function Brand() {
  return <NavLink to="/" className="flex items-center gap-2 px-2 text-base font-semibold" aria-label="AgroJud Radar"><Radar className="size-6 shrink-0 text-primary" aria-hidden="true" /><span className="brand-name">AgroJud Radar</span></NavLink>;
}
function EnvironmentBar({ environment }: { environment: Environment }) {
  const demo = environment.environment !== "real";
  const Icon = demo ? FlaskConical : Database;
  return <div role="region" aria-label="Ambiente" data-environment={environment.environment} className={cn("flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1 text-xs", demo ? "text-warning-foreground" : "text-muted-foreground")}>
    <span className="flex items-center gap-2 font-medium"><Icon className="size-4" aria-hidden="true" />Ambiente: {environmentLabel(environment.environment)}</span>
    <span>{demo ? "Dados sintéticos; não representam o TJGO." : environment.source_enabled ? "Fonte DataJud habilitada." : `Coleta indisponível: ${environment.source_disabled_reason ?? "fonte desabilitada."}`}</span>
  </div>;
}
export function AppShell() {
  const environment = useEnvironmentQuery();
  const [menuOpen, setMenuOpen] = useState(false);
  return <div className="min-h-svh">
    <a href="#conteudo" className="sr-only z-50 rounded-md bg-card px-3 py-2 focus:not-sr-only focus:fixed focus:top-2 focus:left-2">Pular para o conteúdo</a>
    <aside className="app-sidebar"><Brand /><Navigation /><p className="sidebar-footer mt-auto px-3 pt-8 text-xs text-muted-foreground">Pesquisa e acompanhamento<br />Base local · TJGO</p></aside>
    <div className="app-workspace">
      <header className={cn("sticky top-0 z-40 flex min-h-14 items-center gap-3 border-b px-4 py-3 lg:px-8", environment.data?.environment === "demo" || environment.data?.source_enabled === false ? "bg-warning" : "bg-card")}>
        <Dialog.Root open={menuOpen} onOpenChange={setMenuOpen}>
          <Dialog.Trigger asChild><Button variant="ghost" size="icon" className="lg:hidden" aria-label="Abrir navegação"><Menu aria-hidden="true" /></Button></Dialog.Trigger>
          <Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="nav-drawer" aria-describedby={undefined}>
            <div className="flex items-center justify-between"><Dialog.Title className="text-base font-semibold">AgroJud Radar</Dialog.Title><Dialog.Close asChild><Button variant="ghost" size="icon" aria-label="Fechar navegação"><X aria-hidden="true" /></Button></Dialog.Close></div>
            <Navigation onNavigate={() => setMenuOpen(false)} />
          </Dialog.Content></Dialog.Portal>
        </Dialog.Root>
        {environment.data ? <EnvironmentBar environment={environment.data} /> : <span className="text-sm">AgroJud Radar</span>}
      </header>
      <main id="conteudo" tabIndex={-1} className="app-main">
        {environment.data ? <EnvironmentContext.Provider value={environment.data}><Outlet /></EnvironmentContext.Provider> : environment.isError ? <ErrorState title="Não foi possível identificar o ambiente da API" error={environment.error} onRetry={() => void environment.refetch()} retrying={environment.isFetching} /> : <LoadingState label="Identificando o ambiente…" />}
      </main>
    </div>
  </div>;
}
