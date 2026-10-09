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

export function useRetryFragmentMutation(projectId: string | null) {
  const client = useOptionalGraphClient(projectId);
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
    }) => {
      if (client === null) {
        throw new Error("A project ID is required to retry a fragment.");
      }
      return client.retryFragment(revisionId, idempotencyKey);
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: graphKeys.project(projectId) }),
  });
}

/**
 * Any graph request that 409s on a stale `graphVersion` surfaces a
 * `GraphVersionConflictError`. The `/graph-version-mismatch` problem body
 * carries the current version only in free-text `detail`, so there is no
 * structured version to dedupe on. Instead this guard tracks a local,
 * monotonically increasing "conflict generation" ref: every *new* conflict
 * occurrence bumps the generation and is the one that gets to act on it, so
 * a conflict that is still being handled (generation unchanged) never
 * re-triggers, while a later, distinct conflict (generation advances
 * again) does.
 *
 * "New occurrence" is decided by comparing the actual conflict error
 * object references against the previous render's, not just a rising edge
 * of "is there a conflict right now" — a retry whose request 409s again
 * (e.g. the user clicking "Retry" in `NodeDetailPanel` while the graph is
 * still mid-merge) produces a brand-new `GraphVersionConflictError`
 * instance without `hasConflict` ever dropping back to `false` in between,
 * so a plain rising-edge check would silently stop refetching the project
 * after the very first conflict and leave the UI stuck. Comparing error
 * references instead catches that retried-and-409'd-again case too.
 *
 * On each new generation, the entire `["graph", projectId]` prefix is
 * invalidated (satisfying the "409 invalidates every graph query" global
 * constraint — e.g. a cached `highlight` result with no `graphVersion` in
 * its key is still marked stale) but with `refetchType: "none"`, so nothing
 * refetches yet. Only `GET /project` is then explicitly refetched — this
 * avoids re-issuing the still-stale-versioned query that just 409'd (which
 * would 409 again before the new version is known). Once `GET /project`
 * resolves with the current `graphVersion`, callers that thread that
 * version into their own query keys (e.g. `useGraphOverview`) naturally
 * compute a new key and fetch fresh data.
 */
export function useGraphVersionGuard(
  projectId: string | null,
  errors: readonly unknown[],
): void {
  const queryClient = useQueryClient();
  const generationRef = useRef(0);
  const handledGenerationRef = useRef(0);
  const previousConflictErrorsRef = useRef<readonly unknown[]>([]);
  const conflictErrors = errors.filter(
    (error) => error instanceof GraphVersionConflictError,
  );
  const previousConflictErrors = previousConflictErrorsRef.current;
  const isNewConflict =
    conflictErrors.length > 0 &&
    (conflictErrors.length !== previousConflictErrors.length ||
      conflictErrors.some(
        (error, index) => error !== previousConflictErrors[index],
      ));

  // Bump the generation only when the set of conflict errors actually
  // changed (a brand-new conflict, or a retried one that 409'd again with
  // a new error instance) — not on every render while the same conflict
  // object is still being handled.
  if (isNewConflict) {
    generationRef.current += 1;
  }
  previousConflictErrorsRef.current = conflictErrors;
  const generation = generationRef.current;

  useEffect(() => {
    if (generation === 0 || handledGenerationRef.current >= generation) return;
    handledGenerationRef.current = generation;
    if (projectId === null) return;
    void queryClient.invalidateQueries({
      queryKey: graphKeys.all(projectId),
      refetchType: "none",
    });
    void queryClient.refetchQueries({
      queryKey: graphKeys.project(projectId),
      exact: true,
    });
  }, [generation, projectId, queryClient]);
}
