import { createBrowserRouter } from "react-router";

import { AppShell } from "@/app/app-shell";
import { JobDetailPage } from "@/pages/job-detail-page";
import { JobsPage } from "@/pages/jobs-page";
import { NotFoundPage } from "@/pages/not-found-page";
import { NewsPage } from "@/pages/news-page";
import { ProcessDetailPage } from "@/pages/process-detail-page";
import { ProcessesPage } from "@/pages/processes-page";
import { RadarPage } from "@/pages/radar-page";
import { WatchlistPage } from "@/pages/watchlist-page";

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
      { path: "radar", element: <RadarPage /> },
      { path: "jobs", element: <JobsPage /> },
      { path: "jobs/:jobId", element: <JobDetailPage /> },
      { path: "processes", element: <ProcessesPage /> },
      { path: "processes/:processId", element: <ProcessDetailPage /> },
      { path: "watchlist", element: <WatchlistPage /> },
      { path: "news", element: <NewsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
