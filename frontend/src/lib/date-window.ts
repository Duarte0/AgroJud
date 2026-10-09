import { DISPLAY_TIME_ZONE } from "@/lib/format";

/** Default radar window: last 12 calendar months of filing, ending before today (local). */
export const DEFAULT_WINDOW_MONTHS = 12;

export function localToday(now: Date = new Date()): string {
  // en-CA renders YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

function parseIsoDate(value: string): { year: number; month: number; day: number } {
  const [year, month, day] = value.split("-").map(Number);
  return { year: year ?? 1970, month: month ?? 1, day: day ?? 1 };
}

function toIsoDate(year: number, month: number, day: number): string {
  return [
    String(year).padStart(4, "0"),
    String(month).padStart(2, "0"),
    String(day).padStart(2, "0"),
  ].join("-");
}

function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

export function subtractMonths(isoDate: string, months: number): string {
  const { year, month, day } = parseIsoDate(isoDate);
  const index = year * 12 + (month - 1) - months;
  const targetYear = Math.floor(index / 12);
  const targetMonth = (index % 12) + 1;
  return toIsoDate(targetYear, targetMonth, Math.min(day, daysInMonth(targetYear, targetMonth)));
}

export function previousDay(isoDate: string): string {
  const { year, month, day } = parseIsoDate(isoDate);
  const date = new Date(Date.UTC(year, month - 1, day - 1));
  return toIsoDate(date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate());
}

/** Suggested editable dates equivalent to the server default (inclusive end). */
export function defaultWindow(now: Date = new Date()): { from: string; through: string } {
  const today = localToday(now);
  return { from: subtractMonths(today, DEFAULT_WINDOW_MONTHS), through: previousDay(today) };
}
