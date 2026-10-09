import {
  attemptOutcomeLabel,
  dateStatusLabel,
  formatCalendarDate,
  formatCnj,
  formatDateTime,
  jobStatusLabel,
  NOT_INFORMED,
  reasonLabel,
} from "@/lib/format";

describe("format", () => {
  it("shows absent dates as 'Não informado' instead of the current date", () => {
    expect(formatDateTime(null)).toBe(NOT_INFORMED);
    expect(formatDateTime(undefined)).toBe(NOT_INFORMED);
    expect(formatCalendarDate(null)).toBe(NOT_INFORMED);
  });

  it("formats instants in America/Sao_Paulo", () => {
    expect(formatDateTime("2025-01-15T02:30:00Z")).toBe("14/01/2025, 23:30");
  });

  it("formats calendar dates without time zone shifts", () => {
    expect(formatCalendarDate("2025-01-01")).toBe("01/01/2025");
  });

  it("formats 20-digit CNJ numbers and keeps other values", () => {
    expect(formatCnj("00000010020268090001")).toBe("0000001-00.2026.8.09.0001");
    expect(formatCnj("123")).toBe("123");
  });

  it("labels operational states in Portuguese", () => {
    expect(jobStatusLabel("retry_wait")).toBe("Aguardando nova tentativa");
    expect(jobStatusLabel("partial")).toBe("Parcial");
    expect(reasonLabel("limit")).toBe("Limite de registros da execução atingido");
    expect(reasonLabel("unknown_code")).toBe("unknown_code");
    expect(reasonLabel(null)).toBeNull();
    expect(dateStatusLabel("timezone_ambiguous")).toMatch(/ambígua/i);
  });
});

describe("attemptOutcomeLabel", () => {
  it("translates known outcomes and keeps unknown codes", () => {
    expect(attemptOutcomeLabel(null)).toBe("Em andamento");
    expect(attemptOutcomeLabel("completed")).toBe("Concluído");
    expect(attemptOutcomeLabel("lease_lost")).toBe("lease_lost");
  });
});
