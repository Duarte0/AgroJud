import type { TribunalAvailability } from "@/api/types";

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

  return (
    <fieldset className="space-y-3" disabled={disabled}>
      <legend className="text-sm font-semibold">{label}</legend>
      <p className="text-xs text-muted-foreground">
        As opções habilitadas foram validadas para a capacidade desta consulta. As demais mostram
        o motivo do bloqueio.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {items.map((item) => {
          const reasonId = `tribunal-reason-${item.tribunal.toLowerCase()}`;
          const stateLabel = item.preset_state
            ? `S1/S2 ${item.s1_s2_state} · S5 ${item.preset_state}`
            : `S1/S2 ${item.s1_s2_state}`;
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
                  {item.enabled ? "Disponível para esta consulta." : item.reason ?? "Capacidade não validada."}
                </span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
