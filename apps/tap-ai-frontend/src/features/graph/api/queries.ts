import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { createGraphClient } from "./client";

export const graphKeys = {
  active: (projectId: string | null, sources: readonly string[]) =>
    ["graph", projectId, "active", ...sources] as const,
  search: (
    projectId: string | null,
    snapshotId: string | null,
    query: string,
  ) => ["graph", projectId, snapshotId, "search", query] as const,
};

export function useActiveGraph(
  projectId: string | null,
  sourceRevisionIds: string[],
) {
  const client = useMemo(
    () => (projectId ? createGraphClient(projectId) : null),
    [projectId],
  );
  return useQuery({
    queryKey: graphKeys.active(projectId, sourceRevisionIds),
    enabled: client !== null && sourceRevisionIds.length > 0,
    queryFn: ({ signal }) => client!.active(sourceRevisionIds, signal),
    retry: false,
  });
}

export function useGraphSearch(
  projectId: string | null,
  snapshotId: string | null,
  query: string,
) {
  const client = useMemo(
    () => (projectId ? createGraphClient(projectId) : null),
    [projectId],
  );
  return useQuery({
    queryKey: graphKeys.search(projectId, snapshotId, query),
    enabled: client !== null && snapshotId !== null && query.trim().length > 0,
    queryFn: ({ signal }) => client!.search(snapshotId!, query, 50, signal),
    retry: false,
  });
}
