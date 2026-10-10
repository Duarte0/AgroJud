import { createBrowserRouter } from "react-router";

import { AppShell } from "@/app/app-shell";

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      {
        index: true,
        lazy: async () => ({
          Component: (await import("@/pages/overview-page")).OverviewPage,
        }),
      },
      {
        path: "radar",
        lazy: async () => ({ Component: (await import("@/pages/radar-page")).RadarPage }),
      },
      {
        path: "jobs",
        lazy: async () => ({ Component: (await import("@/pages/jobs-page")).JobsPage }),
      },
      {
        path: "jobs/:jobId",
        lazy: async () => ({ Component: (await import("@/pages/job-detail-page")).JobDetailPage }),
      },
      {
        path: "processes",
        lazy: async () => ({ Component: (await import("@/pages/processes-page")).ProcessesPage }),
      },
      {
        path: "processes/:processId",
        lazy: async () => ({ Component: (await import("@/pages/process-detail-page")).ProcessDetailPage }),
      },
      {
        path: "watchlist",
        lazy: async () => ({ Component: (await import("@/pages/watchlist-page")).WatchlistPage }),
      },
      {
        path: "news",
        lazy: async () => ({ Component: (await import("@/pages/news-page")).NewsPage }),
      },
      {
        path: "*",
        lazy: async () => ({ Component: (await import("@/pages/not-found-page")).NotFoundPage }),
      },
    ],
  },
]);
