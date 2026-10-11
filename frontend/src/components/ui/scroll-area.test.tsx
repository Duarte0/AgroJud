import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ScrollArea } from "@/components/ui/scroll-area";

describe("ScrollArea", () => {
  it("exposes a labelled, keyboard-focusable vertical scroll region", async () => {
    render(
      <ScrollArea label="Movimentações do processo">
        <p>Movimento sintético</p>
      </ScrollArea>,
    );

    const region = screen.getByRole("region", { name: "Movimentações do processo" });
    expect(region).toHaveAttribute("tabindex", "0");
    expect(region).toHaveClass(
      "max-h-[min(70vh,48rem)]",
      "overflow-y-auto",
      "overscroll-contain",
    );
    expect(region).toHaveTextContent("Movimento sintético");
    await userEvent.tab();
    expect(region).toHaveFocus();
  });
});
