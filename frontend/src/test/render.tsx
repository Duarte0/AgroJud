import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider, useLocation } from "react-router";

import type { Environment } from "@/api/types";
import { EnvironmentContext } from "@/app/environment-context";
import { DEMO_ENVIRONMENT } from "@/test/factories";

export type MockHandler = (request: Request) => Response | Promise<Response>;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function apiError(status: number, code: string, message: string): Response {
  return json({ error: { code, message, request_id: "req-test", details: null } }, status);
}

/** Routes fetch calls by "METHOD /path"; unknown routes fail the test loudly. */
export function mockApi(routes: Record<string, MockHandler>) {
  const calls: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const request = input instanceof Request ? input : new Request(input);
    calls.push(request);
    const url = new URL(request.url);
    const handler = routes[`${request.method} ${url.pathname}`];
    if (!handler) throw new Error(`Rota não simulada: ${request.method} ${url.pathname}`);
    return handler(request);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    calls,
    count: (method: string, path: string) =>
      calls.filter((call) => call.method === method && new URL(call.url).pathname === path)
        .length,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

export function renderRoute(
  element: ReactElement,
  {
    path,
    url,
    environment = DEMO_ENVIRONMENT,
  }: { path: string; url: string; environment?: Environment },
) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  const wrap = (child: ReactElement) => (
    <EnvironmentContext.Provider value={environment}>
      {child}
      <LocationProbe />
    </EnvironmentContext.Provider>
  );
  const router = createMemoryRouter(
    [
      { path, element: wrap(element) },
      { path: "*", element: wrap(<p>Outra rota</p>) },
    ],
    { initialEntries: [url] },
  );
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...view, client, router };
}
