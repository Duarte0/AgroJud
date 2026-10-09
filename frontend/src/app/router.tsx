import { createBrowserRouter, Navigate } from "react-router";

import { AppShell } from "@/app/app-shell";
import { JobDetailPage } from "@/pages/job-detail-page";
import { JobsPage } from "@/pages/jobs-page";
import { NotFoundPage } from "@/pages/not-found-page";
import { ProcessDetailPage } from "@/pages/process-detail-page";
import { ProcessesPage } from "@/pages/processes-page";
import { RadarPage } from "@/pages/radar-page";

export const router = createBrowserRouter([
  {
    element: <AppShell />,
    children: [
      // No overview exists yet; the root opens the radar.
      { index: true, element: <Navigate to="/radar" replace /> },
      { path: "radar", element: <RadarPage /> },
      { path: "jobs", element: <JobsPage /> },
      { path: "jobs/:jobId", element: <JobDetailPage /> },
      { path: "processes", element: <ProcessesPage /> },
      { path: "processes/:processId", element: <ProcessDetailPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
