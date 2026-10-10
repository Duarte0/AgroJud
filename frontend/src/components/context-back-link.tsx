import { ArrowLeft } from "lucide-react";
import { Link, useLocation } from "react-router";

export function ContextBackLink({ fallback, label }: { fallback: string; label: string }) {
  const { state } = useLocation();
  const from: unknown = state?.from;
  const safe = typeof from === "string" && /^\/(processes|jobs|news|watchlist)(\?|$)/.test(from);
  return <Link to={safe ? from : fallback} className="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-primary"><ArrowLeft className="size-4" aria-hidden="true" />{label}</Link>;
}
