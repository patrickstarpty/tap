import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";
import { createGraphClient, GraphVersionConflictError } from "./client";

function sorted(values: readonly string[]): string[] {
  return [...values].sort();
}

export const graphKeys = {
  all: (projectId: string | null) => ["graph", projectId] as const,
  project: (projectId: string | null) =>
    ["graph", projectId, "project"] as const,
  overview: (
    projectId: string | null,
    graphVersion: number | null,
    sourceRevisionIds: readonly string[],
    communityIds: readonly string[],
    nodeLimit: number,
  ) =>
    [
      "graph",
      projectId,
      "overview",
      graphVersion,
      sorted(sourceRevisionIds),
      sorted(communityIds),
      nodeLimit,
    ] as const,
  search: (
    projectId: string | null,
    graphVersion: number | null,
    query: string,
    sourceRevisionIds: readonly string[],
  ) =>
    [
      "graph",
      projectId,
      "search",
      graphVersion,
      query,
      sorted(sourceRevisionIds),
    ] as const,
  node: (
    projectId: string | null,
    graphVersion: number | null,
    nodeId: string,
  ) => ["graph", projectId, "node", graphVersion, nodeId] as const,
  highlight: (projectId: string | null, edgeIds: readonly string[]) =>
    ["graph", projectId, "highlight", sorted(edgeIds)] as const,
};

function useOptionalGraphClient(projectId: string | null) {
  return useMemo(
    () => (projectId === null ? null : createGraphClient(projectId)),
    [projectId],
  );
}

export function useGraphProject(projectId: string | null) {
  const client = useOptionalGraphClient(projectId);
  return useQuery({
    queryKey: graphKeys.project(projectId),
    enabled: client !== null,
    queryFn: ({ signal }) => client!.project(signal),
    retry: false,
  });
}

export function useGraphOverview(
  projectId: string | null,
  graphVersion: number | null,
  {
    sourceRevisionIds,
    communityIds,
    nodeLimit,
  }: {
    sourceRevisionIds: readonly string[];
    communityIds: readonly string[];
    nodeLimit: number;
  },
) {
  const client = useOptionalGraphClient(projectId);
  return useQuery({
    queryKey: graphKeys.overview(
      projectId,
      graphVersion,
      sourceRevisionIds,
      communityIds,
      nodeLimit,
    ),
    enabled: client !== null,
    queryFn: ({ signal }) =>
      client!.overview(
        {
          sourceRevisionIds: [...sourceRevisionIds],
          communityIds: [...communityIds],
          nodeLimit,
          graphVersion,
        },
        signal,
      ),
    retry: false,
  });
}

export function useGraphSearch(
  projectId: string | null,
  graphVersion: number | null,
  query: string,
  sourceRevisionIds: readonly string[],
) {
  const client = useOptionalGraphClient(projectId);
  const trimmedQuery = query.trim();
  return useQuery({
    queryKey: graphKeys.search(
      projectId,
      graphVersion,
      trimmedQuery,
      sourceRevisionIds,
    ),
    enabled: client !== null && trimmedQuery.length > 0,
    queryFn: ({ signal }) =>
      client!.query(
        {
          query: trimmedQuery,
          sourceRevisionIds: [...sourceRevisionIds],
          graphVersion,
        },
        signal,
      ),
    retry: false,
  });
}

export function useGraphNode(
  projectId: string | null,
  graphVersion: number | null,
  nodeId: string | null,
) {
  const client = useOptionalGraphClient(projectId);
  return useQuery({
    queryKey: graphKeys.node(projectId, graphVersion, nodeId ?? ""),
    enabled: client !== null && nodeId !== null,
    queryFn: ({ signal }) => client!.node(nodeId ?? "", graphVersion, signal),
    retry: false,
  });
}

export function useGraphHighlight(
  projectId: string | null,
  edgeIds: readonly string[],
) {
  const client = useOptionalGraphClient(projectId);
  return useQuery({
    queryKey: graphKeys.highlight(projectId, edgeIds),
    enabled: client !== null && edgeIds.length > 0,
    queryFn: ({ signal }) => client!.highlight([...edgeIds], signal),
    retry: false,
  });
}

export function useRetryFragmentMutation(projectId: string) {
  const client = useMemo(() => createGraphClient(projectId), [projectId]);
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["graph", projectId, "retry-fragment"],
    retry: false,
    mutationFn: ({
      revisionId,
      idempotencyKey,
    }: {
      revisionId: string;
      idempotencyKey: string;
    }) => client.retryFragment(revisionId, idempotencyKey),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: graphKeys.project(projectId) }),
  });
}

/**
 * Any graph request that 409s on a stale `graphVersion` surfaces a
 * `GraphVersionConflictError`. The `/graph-version-mismatch` problem body
 * carries the current version only in free-text `detail`, so there is no
 * structured version to dedupe on. Instead this guard tracks a local,
 * monotonically increasing "conflict generation": each rising edge of
 * `hasConflict` (no conflict -> conflict) bumps the generation and fires
 * exactly one invalidate/refetch for it, regardless of how many renders
 * observe the same ongoing conflict.
 *
 * The `["graph", projectId]` prefix is marked stale without an immediate
 * refetch (`refetchType: "none"`), and only `GET /project` is explicitly
 * refetched. This avoids re-issuing the still-stale-versioned query that
 * just 409'd (which would 409 again before the new version is known).
 * Once `GET /project` resolves with the current `graphVersion`, callers
 * that thread that version into their query keys (e.g. `useGraphOverview`)
 * naturally compute a new key and fetch fresh data.
 */
export function useGraphVersionGuard(
  projectId: string | null,
  errors: readonly unknown[],
): void {
  const queryClient = useQueryClient();
  const conflictGenerationRef = useRef(0);
  const triggeredGenerationRef = useRef(0);
  const hasConflict = errors.some(
    (error) => error instanceof GraphVersionConflictError,
  );

  useEffect(() => {
    if (!hasConflict) return;
    conflictGenerationRef.current += 1;
    if (triggeredGenerationRef.current >= conflictGenerationRef.current) return;
    triggeredGenerationRef.current = conflictGenerationRef.current;
    if (projectId === null) return;
    void queryClient.refetchQueries({
      queryKey: graphKeys.project(projectId),
      exact: true,
    });
  }, [hasConflict, projectId, queryClient]);
}
