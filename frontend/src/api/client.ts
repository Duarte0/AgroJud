import createClient from "openapi-fetch";

import type { paths } from "@/api-schema";
import type { ErrorBody } from "@/api/types";

/** Same-origin client: Vite proxies /api to the local backend. */
export const api = createClient<paths>({
  baseUrl: globalThis.location?.origin ?? "",
  // Resolved per call so tests and instrumentation can replace fetch.
  fetch: (input) => globalThis.fetch(input),
});

/** The API answered with its uniform error envelope (or an unexpected status). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(status: number, body: Partial<ErrorBody> | null) {
    super(body?.message ?? `A API respondeu com status ${status}.`);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.code ?? "unexpected_response";
    this.requestId = body?.request_id ?? null;
  }
}

/** The browser could not reach the API; nothing about the data is known. */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("Não foi possível conectar à API local.", { cause });
    this.name = "NetworkError";
  }
}

type FetchResult<T> = {
  data?: T;
  error?: unknown;
  response: Response;
};

function errorBody(error: unknown): Partial<ErrorBody> | null {
  if (error && typeof error === "object" && "error" in error) {
    const body = (error as { error: unknown }).error;
    if (body && typeof body === "object") return body as Partial<ErrorBody>;
  }
  return null;
}

/** Turns an openapi-fetch result into data or a classified exception. */
export async function request<T>(call: () => Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await call();
  } catch (cause) {
    throw new NetworkError(cause);
  }
  if (!result.response.ok || result.data === undefined) {
    throw new ApiError(result.response.status, errorBody(result.error));
  }
  return result.data;
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** Reads are retried for transient failures only; 4xx answers are final. */
export function shouldRetryRead(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 2;
}

export function describeError(error: unknown): string {
  if (error instanceof NetworkError) {
    return "A API local não respondeu. Verifique se ela está em execução.";
  }
  if (error instanceof ApiError) {
    if (error.status === 503) return "O banco de dados está temporariamente indisponível.";
    return error.message;
  }
  return "Ocorreu um erro inesperado.";
}
