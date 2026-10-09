import {
  keepPreviousData,
  QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api, request, shouldRetryRead } from "@/api/client";
import type {
  CreateJobRequest,
  EnvironmentName,
  JobCommand,
  JobCommandResult,
  JobListQuery,
  NewsListQuery,
  ProcessDetail,
  ProcessNewsStatusPatch,
  ProcessListQuery,
  ProcessWatch,
  ProcessTriagePatch,
  SignalRunRequest,
  WatchlistQuery,
} from "@/api/types";
import { useCurrentEnvironment } from "@/app/environment-context";
import { jobListPollInterval, jobPollInterval } from "@/lib/job-progress";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetryRead, refetchOnWindowFocus: true },
      // Commands are never retried automatically: a retry could enqueue twice.
      mutations: { retry: false },
    },
  });
}

/** Every data key starts with the environment so demo and real caches never mix. */
export const queryKeys = {
  environment: ["environment"] as const,
  presets: (env: EnvironmentName) => [env, "presets"] as const,
  jobs: (env: EnvironmentName) => [env, "jobs"] as const,
  jobList: (env: EnvironmentName, query: JobListQuery) => [env, "jobs", "list", query] as const,
  job: (env: EnvironmentName, id: string) => [env, "jobs", "detail", id] as const,
  processes: (env: EnvironmentName) => [env, "processes"] as const,
  processList: (env: EnvironmentName, query: ProcessListQuery) =>
    [env, "processes", "list", query] as const,
  process: (env: EnvironmentName, id: string) => [env, "processes", "detail", id] as const,
  processWatch: (env: EnvironmentName, id: string) =>
    [env, "processes", "detail", id, "watch"] as const,
  watchlist: (env: EnvironmentName, query?: WatchlistQuery) =>
    query ? ([env, "watchlist", query] as const) : ([env, "watchlist"] as const),
  news: (env: EnvironmentName, query?: NewsListQuery) =>
    query ? ([env, "news", query] as const) : ([env, "news"] as const),
  triageHistory: (env: EnvironmentName, id: string, page?: number) =>
    page === undefined
      ? ([env, "processes", "detail", id, "triage-history"] as const)
      : ([env, "processes", "detail", id, "triage-history", page] as const),
  representations: (env: EnvironmentName, id: string, page: number) =>
    [env, "processes", "detail", id, "representations", page] as const,
  movements: (env: EnvironmentName, id: string, page: number, occurrenceId?: string) =>
    [env, "processes", "detail", id, "movements", page, occurrenceId] as const,
  signals: (env: EnvironmentName) => [env, "processes", "signals"] as const,
  processSignals: (env: EnvironmentName, id: string) =>
    [env, "processes", "detail", id, "signals"] as const,
};

export function useEnvironmentQuery() {
  return useQuery({
    queryKey: queryKeys.environment,
    queryFn: ({ signal }) => request(() => api.GET("/api/v1/environment", { signal })),
    staleTime: 60_000,
  });
}

export function usePresets() {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.presets(environment),
    queryFn: ({ signal }) => request(() => api.GET("/api/v1/presets", { signal })),
    staleTime: 60_000,
  });
}

export function useJobList(query: JobListQuery) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.jobList(environment, query),
    queryFn: ({ signal }) =>
      request(() => api.GET("/api/v1/jobs", { params: { query }, signal })),
    placeholderData: keepPreviousData,
    refetchInterval: (current) => jobListPollInterval(current.state.data?.items),
  });
}

export function useJob(jobId: string) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.job(environment, jobId),
    queryFn: ({ signal }) =>
      request(() =>
        api.GET("/api/v1/jobs/{job_id}", { params: { path: { job_id: jobId } }, signal }),
      ),
    refetchInterval: (current) => jobPollInterval(current.state.data?.status),
  });
}

export function useProcessList(query: ProcessListQuery) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.processList(environment, query),
    queryFn: ({ signal }) =>
      request(() => api.GET("/api/v1/processes", { params: { query }, signal })),
    placeholderData: keepPreviousData,
  });
}

export function useProcess(processId: string) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.process(environment, processId),
    queryFn: ({ signal }) =>
      request(() =>
        api.GET("/api/v1/processes/{process_id}", {
          params: { path: { process_id: processId } },
          signal,
        }),
      ),
  });
}

export function useProcessWatch(processId: string) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.processWatch(environment, processId),
    queryFn: ({ signal }) =>
      request(() =>
        api.GET("/api/v1/processes/{process_id}/watch", {
          params: { path: { process_id: processId } },
          signal,
        }),
      ),
    refetchInterval: (current) =>
      current.state.data?.last_refresh?.state === "pending" ? 3_000 : false,
  });
}

export function useWatchlist(query: WatchlistQuery) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.watchlist(environment, query),
    queryFn: ({ signal }) =>
      request(() => api.GET("/api/v1/watchlist", { params: { query }, signal })),
    placeholderData: keepPreviousData,
    refetchInterval: (current) =>
      current.state.data?.items.some((item) => item.last_refresh?.state === "pending")
        ? 3_000
        : false,
  });
}

export function useNewsList(query: NewsListQuery) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.news(environment, query),
    queryFn: ({ signal }) =>
      request(() => api.GET("/api/v1/news", { params: { query }, signal })),
    placeholderData: keepPreviousData,
  });
}

export function useUpdateNewsStatus() {
  const { environment } = useCurrentEnvironment();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: ProcessNewsStatusPatch["status"] }) =>
      request(() =>
        api.PATCH("/api/v1/news/{news_id}", {
          params: { path: { news_id: id } },
          body: { status },
        }),
      ),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: queryKeys.news(environment) });
    },
  });
}

export function useSetProcessWatch(processId: string) {
  const { environment } = useCurrentEnvironment();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (active: boolean) => {
      const params = { path: { process_id: processId } };
      return active
        ? request(() => api.PUT("/api/v1/processes/{process_id}/watch", { params }))
        : request(() => api.DELETE("/api/v1/processes/{process_id}/watch", { params }));
    },
    onSuccess: async (watch) => {
      client.setQueryData<ProcessWatch>(queryKeys.processWatch(environment, processId), watch);
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.watchlist(environment) }),
        client.invalidateQueries({ queryKey: queryKeys.processes(environment) }),
      ]);
    },
  });
}

export function useRefreshProcess(processId: string) {
  const { environment } = useCurrentEnvironment();
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      request(() =>
        api.POST("/api/v1/processes/{process_id}/refresh", {
          params: { path: { process_id: processId } },
        }),
      ),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.processWatch(environment, processId) }),
        client.invalidateQueries({ queryKey: queryKeys.watchlist(environment) }),
        client.invalidateQueries({ queryKey: queryKeys.jobs(environment) }),
      ]);
    },
  });
}

export function useProcessTriageHistory(processId: string, page: number) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.triageHistory(environment, processId, page),
    queryFn: ({ signal }) =>
      request(() =>
        api.GET("/api/v1/processes/{process_id}/triage-history", {
          params: { path: { process_id: processId }, query: { page, page_size: 20 } },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useUpdateProcessTriage(processId: string) {
  const { environment } = useCurrentEnvironment();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: ProcessTriagePatch) =>
      request(() =>
        api.PATCH("/api/v1/processes/{process_id}/triage", {
          params: { path: { process_id: processId } },
          body,
        }),
      ),
    onSuccess: async (triage) => {
      client.setQueryData<ProcessDetail>(queryKeys.process(environment, processId), (current) =>
        current ? { ...current, triage } : current,
      );
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.processes(environment) }),
        client.invalidateQueries({ queryKey: queryKeys.triageHistory(environment, processId) }),
      ]);
    },
  });
}

export function useRepresentations(processId: string, page: number) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.representations(environment, processId, page),
    queryFn: ({ signal }) =>
      request(() =>
        api.GET("/api/v1/processes/{process_id}/representations", {
          params: { path: { process_id: processId }, query: { page, page_size: 100 } },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useMovements(processId: string, page: number, occurrenceId?: string) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.movements(environment, processId, page, occurrenceId),
    queryFn: ({ signal }) =>
      request(() =>
        api.GET("/api/v1/processes/{process_id}/movements", {
          params: {
            path: { process_id: processId },
            query: { page, page_size: 25, occurrence_id: occurrenceId },
          },
          signal,
        }),
      ),
    placeholderData: keepPreviousData,
  });
}

export function useProcessSignals(processId: string) {
  const { environment } = useCurrentEnvironment();
  return useQuery({
    queryKey: queryKeys.processSignals(environment, processId),
    queryFn: ({ signal }) =>
      request(() =>
        api.GET("/api/v1/processes/{process_id}/signals", {
          params: { path: { process_id: processId }, query: { include_history: true } },
          signal,
        }),
      ),
    refetchInterval: (current) => {
      const status = current.state.data?.latest_run?.status;
      return status === "queued" || status === "running" || status === "retry_wait" ? 3_000 : false;
    },
  });
}

export function useCreateSignalRun(processId: string) {
  const { environment } = useCurrentEnvironment();
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      request(() =>
        api.POST("/api/v1/rule-runs", { body: { process_ids: [processId] } satisfies SignalRunRequest }),
      ),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: queryKeys.processSignals(environment, processId) });
    },
  });
}

export function useResumeSignalRun(processId: string) {
  const { environment } = useCurrentEnvironment();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      request(() =>
        api.POST("/api/v1/rule-runs/{job_id}/resume", {
          params: { path: { job_id: jobId } },
        }),
      ),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: queryKeys.processSignals(environment, processId) });
    },
  });
}

export function useCreateJob() {
  const { environment } = useCurrentEnvironment();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateJobRequest) => request(() => api.POST("/api/v1/jobs", { body })),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: queryKeys.jobs(environment) });
    },
  });
}

function postJobCommand(jobId: string, command: JobCommand): Promise<JobCommandResult> {
  const init = { params: { path: { job_id: jobId } } };
  switch (command) {
    case "cancel":
      return request(() => api.POST("/api/v1/jobs/{job_id}/cancel", init));
    case "resume":
      return request(() => api.POST("/api/v1/jobs/{job_id}/resume", init));
    case "continue":
      return request(() => api.POST("/api/v1/jobs/{job_id}/continue", init));
    case "restart-scan":
      return request(() => api.POST("/api/v1/jobs/{job_id}/restart-scan", init));
  }
}

export function useJobCommand(jobId: string) {
  const { environment } = useCurrentEnvironment();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (command: JobCommand) => postJobCommand(jobId, command),
    onSuccess: async () => {
      // Confirmed by the backend: refresh lists and details so polling restarts
      // from the persisted state rather than from an optimistic guess.
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.jobs(environment) }),
        client.invalidateQueries({ queryKey: queryKeys.processes(environment) }),
      ]);
    },
  });
}
