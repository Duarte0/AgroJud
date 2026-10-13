import type { TribunalAvailability } from "@/api/types";
import { Button } from "@/components/ui/button";

type TribunalOption = TribunalAvailability["tribunals"][number];

export function TribunalSelector({
  items,
  selected,
  onChange,
  disabled = false,
  label = "Tribunais para esta execução",
}: {
  items: TribunalOption[];
  selected: readonly string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
  label?: string;
}) {
  function toggle(tribunal: string, checked: boolean) {
    onChange(
      checked
        ? [...selected, tribunal]
        : selected.filter((item) => item !== tribunal),
    );
  }

  const enabledItems = items.filter((item) => item.enabled);
  const allSelected = enabledItems.length > 0 && enabledItems.every((item) => selected.includes(item.tribunal));
  const scopeLabel = selected.length === items.length
    ? items.length === 33
      ? "Coleta nacional: todos os 33 tribunais."
      : `Todos os ${items.length} tribunais da lista selecionados.`
    : `Consulta em ${selected.length} de ${items.length} tribunais.`;

  return (
    <fieldset className="space-y-3" disabled={disabled}>
      <legend className="text-sm font-semibold">{label}</legend>
      <p className="text-xs text-muted-foreground">
        Escolha um ou mais endpoints da allowlist DataJud. A resposta da consulta será acompanhada
        separadamente por tribunal.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onChange(enabledItems.map((item) => item.tribunal))}
          disabled={disabled || allSelected}
        >
          Selecionar todos ({enabledItems.length})
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onChange([])}
          disabled={disabled || selected.length === 0}
        >
          Limpar seleção
        </Button>
        <span className="text-xs font-medium text-muted-foreground" aria-live="polite">
          {scopeLabel}
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {items.map((item) => {
          const reasonId = `tribunal-reason-${item.tribunal.toLowerCase()}`;
          const stateLabel = item.enabled ? "Disponível" : "Indisponível";
          return (
            <label
              key={item.tribunal}
              className="flex min-w-0 items-start gap-3 rounded-md border px-3 py-2 text-sm"
            >
              <input
                type="checkbox"
                name="tribunal"
                value={item.tribunal}
                checked={selected.includes(item.tribunal)}
                disabled={disabled || !item.enabled}
                aria-describedby={reasonId}
                onChange={(event) => toggle(item.tribunal, event.currentTarget.checked)}
                className="mt-0.5 size-4 shrink-0 accent-primary"
              />
              <span className="min-w-0">
                <span className="flex flex-wrap items-center gap-x-2 font-medium">
                  <span>{item.tribunal}</span>
                  <span className="text-xs font-normal text-muted-foreground">{stateLabel}</span>
                </span>
                <span
                  id={reasonId}
                  className={item.enabled ? "block text-xs text-muted-foreground" : "block text-xs text-warning-foreground"}
                >
                  {item.enabled ? "Endpoint disponível na allowlist." : item.reason ?? "Endpoint indisponível."}
                </span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
