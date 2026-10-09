/** URL search params are the single source of truth for list filters and pages. */

export function readPage(params: URLSearchParams, key = "page"): number {
  const raw = params.get(key);
  if (!raw || !/^\d+$/.test(raw)) return 1;
  const page = Number(raw);
  return Number.isSafeInteger(page) && page >= 1 ? page : 1;
}

export function readText(params: URLSearchParams, key: string): string | undefined {
  const value = params.get(key)?.trim();
  return value ? value : undefined;
}

export function readEnum<T extends string>(
  params: URLSearchParams,
  key: string,
  allowed: readonly T[],
): T | undefined {
  const value = params.get(key);
  return value !== null && (allowed as readonly string[]).includes(value) ? (value as T) : undefined;
}

/** Applies filter values, dropping empty ones, and returns to the first page. */
export function withFilters(
  current: URLSearchParams,
  values: Record<string, string | undefined>,
): URLSearchParams {
  const next = new URLSearchParams(current);
  for (const [key, value] of Object.entries(values)) {
    const trimmed = value?.trim();
    if (trimmed) next.set(key, trimmed);
    else next.delete(key);
  }
  next.delete("page");
  return next;
}

export function withPage(current: URLSearchParams, page: number, key = "page"): URLSearchParams {
  const next = new URLSearchParams(current);
  if (page <= 1) next.delete(key);
  else next.set(key, String(page));
  return next;
}

export function totalPages(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / pageSize));
}
