import { fireEvent, render, screen, within } from "@testing-library/react";

import { TribunalSelector } from "@/components/tribunal-selector";
import type { TribunalAvailability } from "@/api/types";

describe("TribunalSelector", () => {
  it("offers select-all and clear controls over the allowlist", () => {
    const onChange = vi.fn();
    const items: TribunalAvailability["tribunals"] = [
      {
        tribunal: "TJMT",
        s1_s2_state: "NOT_REQUIRED",
        preset_state: "NOT_REQUIRED",
        enabled: true,
        reason: null,
      },
      {
        tribunal: "TRF1",
        s1_s2_state: "NOT_REQUIRED",
        preset_state: "NOT_REQUIRED",
        enabled: true,
        reason: null,
      },
    ];
    const { rerender } = render(<TribunalSelector items={items} selected={[]} onChange={onChange} />);
    const selector = screen.getByRole("group", { name: "Tribunais para esta execução" });

    expect(within(selector).getByText("Consulta em 0 de 2 tribunais.")).toBeInTheDocument();

    fireEvent.click(within(selector).getByRole("button", { name: "Selecionar todos (2)" }));
    expect(onChange).toHaveBeenCalledWith(["TJMT", "TRF1"]);
    rerender(<TribunalSelector items={items} selected={["TJMT", "TRF1"]} onChange={onChange} />);
    expect(within(selector).getByRole("checkbox", { name: /TJMT/ })).toBeChecked();
    expect(within(selector).getByRole("checkbox", { name: /TRF1/ })).toBeChecked();
    expect(within(selector).getByText("Todos os 2 tribunais da lista selecionados.")).toBeInTheDocument();

    fireEvent.click(within(selector).getByRole("button", { name: "Limpar seleção" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});
