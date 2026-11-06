import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { applyWorkflowEvent } from "../model/events";
import {
  createWorkflowClient,
  WorkflowClientError,
  type WorkflowCommandRequest,
  type WorkflowTaskSnapshot,
} from "./client";

export const workflowKeys = {
  all: (projectId: string | null) => ["workflows", projectId] as const,
  task: (projectId: string | null, taskId: string | null) =>
    ["workflows", projectId, "task", taskId] as const,
  node: (
    projectId: string | null,
    taskId: string | null,
    nodeId: string | null,
  ) => ["workflows", projectId, "task", taskId, "node", nodeId] as const,
};
const terminal = new Set(["succeeded", "failed", "canceled", "skipped"]);

export function useWorkflowRuns(projectId: string | null, agentId?: string) {
  const client = useMemo(
    () => (projectId === null ? null : createWorkflowClient(projectId)),
    [projectId],
  );
  return useInfiniteQuery({
    queryKey: [...workflowKeys.all(projectId), "runs", agentId ?? "all"],
    enabled: client !== null,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      client!.list({ agentId, cursor: pageParam, limit: 20, signal }),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  });
}

export function useWorkflowTask(
  projectId: string | null,
  taskId: string | null,
) {
  const cache = useQueryClient();
  const client = useMemo(
    () => (projectId === null ? null : createWorkflowClient(projectId)),
    [projectId],
  );
  const key = workflowKeys.task(projectId, taskId);
  const query = useQuery({
    queryKey: key,
    enabled: client !== null && taskId !== null,
    queryFn: ({ signal }) => client!.snapshot(taskId!, signal),
  });
  const [connection, setConnection] = useState<
    "idle" | "connecting" | "live" | "reconnecting" | "error"
  >("idle");
  const [retryNonce, setRetryNonce] = useState(0);
  useEffect(() => {
    if (client === null || taskId === null || !query.isSuccess) return;
    const abort = new AbortController();
    let stopped = false;
    const run = async () => {
      let cursor =
        cache.getQueryData<WorkflowTaskSnapshot>(key)?.lastSequence ?? 0;
      let attempts = 0;
      setConnection("connecting");
      while (!stopped) {
        try {
          for await (const event of client.stream(
            taskId,
            cursor,
            abort.signal,
          )) {
            if (stopped) return;
            const current = cache.getQueryData<WorkflowTaskSnapshot>(key);
            let next =
              current === undefined ? null : applyWorkflowEvent(current, event);
            if (next === null) {
              next = await client.snapshot(taskId, abort.signal);
              if (stopped) return;
            }
            cache.setQueryData(key, next);
            cursor = next.lastSequence;
            attempts = 0;
            setConnection("live");
          }
          if (
            terminal.has(
              cache.getQueryData<WorkflowTaskSnapshot>(key)?.status ?? "",
            )
          ) {
            setConnection("idle");
            return;
          }
        } catch (error) {
          if (stopped || abort.signal.aborted) return;
          if (error instanceof WorkflowClientError && error.status === 409) {
            try {
              const fresh = await client.snapshot(taskId, abort.signal);
              if (stopped) return;
              cache.setQueryData(key, fresh);
              cursor = fresh.lastSequence;
            } catch {
              /* retry on the next connection attempt */
            }
          }
        }
        attempts += 1;
        if (attempts > 5) {
          setConnection("error");
          return;
        }
        setConnection("reconnecting");
        await new Promise<void>((resolve) =>
          setTimeout(resolve, Math.min(500 * 2 ** (attempts - 1), 4000)),
        );
      }
    };
    void run();
    return () => {
      stopped = true;
      abort.abort();
    };
  }, [cache, client, projectId, taskId, query.isSuccess, retryNonce]);
  return {
    snapshot: query.data,
    isLoading: query.isPending,
    isError: query.isError,
    connection,
    retry: () => {
      void query.refetch();
      setRetryNonce((value) => value + 1);
    },
  };
}

export function useWorkflowNode(
  projectId: string | null,
  taskId: string | null,
  nodeId: string | null,
) {
  const client = useMemo(
    () => (projectId === null ? null : createWorkflowClient(projectId)),
    [projectId],
  );
  return useQuery({
    queryKey: workflowKeys.node(projectId, taskId, nodeId),
    enabled: client !== null && taskId !== null && nodeId !== null,
    queryFn: ({ signal }) => client!.node(taskId!, nodeId!, signal),
  });
}

export function useWorkflowLogs(
  projectId: string | null,
  taskId: string | null,
  nodeId: string | null,
  attempt: number,
) {
  const client = useMemo(
    () => (projectId === null ? null : createWorkflowClient(projectId)),
    [projectId],
  );
  return useInfiniteQuery({
    queryKey: [
      ...workflowKeys.node(projectId, taskId, nodeId),
      "logs",
      attempt,
    ],
    enabled: client !== null && taskId !== null && nodeId !== null,
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      client!.logs(taskId!, nodeId!, attempt, pageParam, signal),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  });
}

export function useWorkflowCommand(
  projectId: string | null,
  taskId: string | null,
) {
  const cache = useQueryClient();
  const client = useMemo(
    () => (projectId === null ? null : createWorkflowClient(projectId)),
    [projectId],
  );
  return useMutation({
    mutationFn: ({
      body,
      key,
    }: {
      body: WorkflowCommandRequest;
      key: string;
    }) => {
      if (client === null || taskId === null)
        throw new Error("Workflow unavailable.");
      return client.command(taskId, body, key);
    },
    onSuccess: () =>
      cache.invalidateQueries({
        queryKey: workflowKeys.task(projectId, taskId),
      }),
  });
}
