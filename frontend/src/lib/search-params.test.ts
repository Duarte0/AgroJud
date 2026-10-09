import { defaultWindow, previousDay, subtractMonths } from "@/lib/date-window";
import { readEnum, readPage, totalPages, withFilters, withPage } from "@/lib/search-params";

describe("search params", () => {
  it("reads only positive integer pages", () => {
    expect(readPage(new URLSearchParams("page=3"))).toBe(3);
    for (const raw of ["page=0", "page=-1", "page=abc", "page=1.5", ""]) {
      expect(readPage(new URLSearchParams(raw))).toBe(1);
    }
  });

  it("accepts only known enum values", () => {
    const params = new URLSearchParams("status=running&kind=bogus");
    expect(readEnum(params, "status", ["running", "failed"])).toBe("running");
    expect(readEnum(params, "kind", ["discovery"])).toBeUndefined();
  });

  it("applies filters, drops empty values and resets the page", () => {
    const next = withFilters(new URLSearchParams("page=4&subject=old&class=x"), {
      subject: " crédito ",
      class: "",
    });
    expect(next.toString()).toBe("subject=cr%C3%A9dito");
  });

  it("stores pages beyond the first only", () => {
    expect(withPage(new URLSearchParams("a=1"), 2).toString()).toBe("a=1&page=2");
    expect(withPage(new URLSearchParams("a=1&page=2"), 1).toString()).toBe("a=1");
    expect(withPage(new URLSearchParams(), 3, "mov_page").toString()).toBe("mov_page=3");
  });

  it("never reports zero pages", () => {
    expect(totalPages(0, 25)).toBe(1);
    expect(totalPages(26, 25)).toBe(2);
  });
});

describe("date window", () => {
  it("subtracts calendar months clamping the day", () => {
    expect(subtractMonths("2026-03-31", 1)).toBe("2026-02-28");
    expect(subtractMonths("2026-10-09", 12)).toBe("2025-10-09");
  });

  it("suggests the last 12 months ending yesterday in America/Sao_Paulo", () => {
    // 01:00 UTC on Oct 9th is still Oct 8th in São Paulo.
    expect(defaultWindow(new Date("2026-10-09T01:00:00Z"))).toEqual({
      from: "2025-10-08",
      through: "2026-10-07",
    });
    expect(previousDay("2026-01-01")).toBe("2025-12-31");
  });
});
