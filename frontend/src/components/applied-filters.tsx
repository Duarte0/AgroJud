import { X } from "lucide-react";

const LABELS: Record<string, string> = {
  process_number: "CNJ", subject: "Assunto", subject_code: "Código do tema", subject_name_exact: "Tema exato",
  class: "Classe", court_unit: "Órgão", preset_id: "Preset", collection_id: "Coleta", signal_category: "Sinal",
  decision: "Triagem", rural_link: "Vínculo rural", followed: "Acompanhado", pending_news: "Novidades pendentes",
};
const VALUES: Record<string, string> = { pending: "Pendente", relevant: "Relevante", discarded: "Descartado", confirmed: "Confirmado manualmente", unconfirmed: "Não confirmado", true: "Sim", false: "Não" };
export function AppliedFilters({ values, onRemove }: { values: Record<string, string>; onRemove: (key: string) => void }) {
  const entries = Object.entries(values).filter(([, value]) => value);
  if (!entries.length) return null;
  return <ul aria-label="Filtros aplicados" className="flex flex-wrap gap-2">{entries.map(([key, value]) => (
    <li key={key}><button type="button" className="filter-chip" onClick={() => onRemove(key)} aria-label={`Remover filtro ${LABELS[key] ?? key}`}>
      <span>{LABELS[key] ?? key}: {VALUES[value] ?? value}</span><X className="size-3 shrink-0" aria-hidden="true" />
    </button></li>
  ))}</ul>;
}
