import { fireEvent, render, screen } from "@testing-library/react";

import { TribunalSelector } from "@/components/tribunal-selector";
import type { TribunalAvailability } from "@/api/types";

describe("TribunalSelector", () => {
  it("allows validated targets and explains disabled capabilities", () => {
    const onChange = vi.fn();
    const items: TribunalAvailability["tribunals"] = [
      {
        tribunal: "TJMT",
        s1_s2_state: "VALIDATED",
        preset_state: "VALIDATED",
        enabled: true,
        reason: null,
      },
      {
        tribunal: "TRF1",
        s1_s2_state: "VALIDATED",
        preset_state: "INCONCLUSIVE",
        enabled: false,
        reason: "S5: INCONCLUSIVE para TRF1/rural.credito_contratos.",
      },
    ];
    render(<TribunalSelector items={items} selected={[]} onChange={onChange} />);

    const validated = screen.getByRole("checkbox", { name: /TJMT/ });
    const blocked = screen.getByRole("checkbox", { name: /TRF1/ });
    expect(validated).toBeEnabled();
    expect(blocked).toBeDisabled();
    expect(screen.getByText(/S5: INCONCLUSIVE/)).toBeInTheDocument();

    fireEvent.click(validated);
    expect(onChange).toHaveBeenCalledWith(["TJMT"]);
  });
});
