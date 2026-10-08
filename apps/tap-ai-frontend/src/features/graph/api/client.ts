import createOpenApiClient from "openapi-fetch";
import type { components, paths } from "../../../shared/api/generated/schema";

export type GraphSnapshotPage = components["schemas"]["GraphSnapshotPage"];
export type GraphSubgraph = components["schemas"]["GraphSubgraphView"];

export interface GraphClient {
  active(
    sourceRevisionIds: string[],
    signal?: AbortSignal,
  ): Promise<GraphSnapshotPage>;
  search(
    snapshotId: string,
    query: string,
    nodeLimit?: number,
    signal?: AbortSignal,
  ): Promise<GraphSubgraph>;
  neighbors(
    snapshotId: string,
    nodeId: string,
    depth?: number,
    nodeLimit?: number,
    signal?: AbortSignal,
  ): Promise<GraphSubgraph>;
  path(
    snapshotId: string,
    sourceNodeId: string,
    targetNodeId: string,
    nodeLimit?: number,
    signal?: AbortSignal,
  ): Promise<GraphSubgraph>;
}

export function createGraphClient(
  projectId: string,
  baseUrl = "",
): GraphClient {
  if (!projectId.trim())
    throw new Error("A project ID is required for Graph requests.");
  const http = createOpenApiClient<paths>({ baseUrl });
  const pathProject = { project_id: projectId };
  return {
    async active(sourceRevisionIds, signal) {
      const result = await http.GET(
        "/api/v1/projects/{project_id}/knowledge/graph/snapshots",
        {
          params: {
            path: pathProject,
            query: { sourceRevisionId: sourceRevisionIds },
          },
          signal,
        },
      );
      if (!result.data) throw new Error("Graph is unavailable.");
      return result.data;
    },
    async search(snapshotId, query, nodeLimit = 50, signal) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/knowledge/graph/query",
        {
          params: { path: pathProject },
          body: { snapshotId, query, nodeLimit },
          signal,
        },
      );
      if (!result.data) throw new Error("Graph search failed.");
      // `snapshotId` is always supplied here, so the server always takes the
      // legacy fragment-scoped branch and the response always matches
      // `GraphSubgraph`; the generated type is a union only because the
      // project-graph branch (unused by this client) returns a different shape.
      return result.data as GraphSubgraph;
    },
    async neighbors(snapshotId, nodeId, depth = 1, nodeLimit = 50, signal) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/knowledge/graph/nodes/{node_id}/neighbors",
        {
          params: { path: { ...pathProject, node_id: nodeId } },
          body: { snapshotId, depth, nodeLimit },
          signal,
        },
      );
      if (!result.data) throw new Error("Graph neighbors failed.");
      return result.data;
    },
    async path(snapshotId, sourceNodeId, targetNodeId, nodeLimit = 50, signal) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/knowledge/graph/path",
        {
          params: { path: pathProject },
          body: { snapshotId, sourceNodeId, targetNodeId, nodeLimit },
          signal,
        },
      );
      if (!result.data) throw new Error("Graph path failed.");
      // Same union narrowing as `search` above: `snapshotId` is always
      // supplied here, so the response always matches `GraphSubgraph`.
      return result.data as GraphSubgraph;
    },
  };
}
