import createOpenApiClient from "openapi-fetch";
import type { components, paths } from "../../../shared/api/generated/schema";
import type {
  GraphNodeDetail,
  GraphProject,
  GraphSubgraph,
} from "../model/graph";

export type GraphFragmentRetry =
  components["schemas"]["GraphFragmentRetryView"];

/**
 * The `/graph-version-mismatch` problem body only carries the current
 * version inside a free-text `detail` string (never a structured field), so
 * this error intentionally carries no `currentVersion`. Callers recover by
 * refetching `GET /project` instead of parsing `detail`.
 */
export class GraphVersionConflictError extends Error {
  constructor() {
    super("The project graph version has changed.");
    this.name = "GraphVersionConflictError";
  }
}

export interface GraphClient {
  project(signal?: AbortSignal): Promise<GraphProject>;
  overview(
    input: {
      sourceRevisionIds: string[];
      communityIds: string[];
      nodeLimit: number;
      graphVersion?: number | null;
    },
    signal?: AbortSignal,
  ): Promise<GraphSubgraph>;
  query(
    input: {
      query: string;
      sourceRevisionIds: string[];
      nodeLimit?: number;
      graphVersion?: number | null;
    },
    signal?: AbortSignal,
  ): Promise<GraphSubgraph>;
  neighbors(
    nodeId: string,
    input: {
      depth?: 1 | 2;
      nodeLimit?: number;
      graphVersion?: number | null;
    },
    signal?: AbortSignal,
  ): Promise<GraphSubgraph>;
  path(
    input: {
      sourceNodeId: string;
      targetNodeId: string;
      nodeLimit?: number;
      graphVersion?: number | null;
    },
    signal?: AbortSignal,
  ): Promise<GraphSubgraph>;
  node(
    nodeId: string,
    graphVersion?: number | null,
    signal?: AbortSignal,
  ): Promise<GraphNodeDetail>;
  highlight(edgeIds: string[], signal?: AbortSignal): Promise<GraphSubgraph>;
  retryFragment(revisionId: string, idempotencyKey: string): Promise<void>;
}

// Mirrors the `origin()` / `resolveApiBaseUrl()` pattern from
// `features/knowledge/api/client.ts`. Duplicated rather than imported: the
// repository's `no-feature-to-feature` dependency-cruiser rule forbids
// `features/graph` importing from `features/knowledge`.
function origin(): string {
  if (typeof window !== "undefined" && window.location.origin !== "null") {
    return window.location.origin;
  }
  return "http://127.0.0.1";
}

function resolveApiBaseUrl(baseUrl = ""): string {
  const resolved = new URL(
    baseUrl.length === 0 ? "/" : baseUrl,
    `${origin()}/`,
  );
  return resolved.toString().replace(/\/$/u, "");
}

export function createGraphClient(
  projectId: string,
  baseUrl = "",
): GraphClient {
  if (!projectId.trim())
    throw new Error("A project ID is required for Graph requests.");
  const http = createOpenApiClient<paths>({
    baseUrl: resolveApiBaseUrl(baseUrl),
  });
  const pathProject = { project_id: projectId };

  async function unwrap<T>(
    promise: Promise<{ data?: T; response: Response }>,
  ): Promise<T> {
    const result = await promise;
    if (result.response.status === 409) {
      throw new GraphVersionConflictError();
    }
    if (result.data === undefined) {
      throw new Error(`Graph request failed (${result.response.status}).`);
    }
    return result.data;
  }

  return {
    async project(signal) {
      return unwrap(
        http.GET("/api/v1/projects/{project_id}/knowledge/graph/project", {
          params: { path: pathProject },
          signal,
        }),
      );
    },
    async overview(
      { sourceRevisionIds, communityIds, nodeLimit, graphVersion },
      signal,
    ) {
      return unwrap(
        http.GET("/api/v1/projects/{project_id}/knowledge/graph/overview", {
          params: {
            path: pathProject,
            query: {
              sourceRevisionId: sourceRevisionIds,
              communityId: communityIds,
              nodeLimit,
              graphVersion: graphVersion ?? undefined,
            },
          },
          signal,
        }),
      );
    },
    async query({ query, sourceRevisionIds, nodeLimit, graphVersion }, signal) {
      const data = await unwrap(
        http.POST("/api/v1/projects/{project_id}/knowledge/graph/query", {
          params: { path: pathProject },
          body: {
            query,
            sourceRevisionIds,
            ...(nodeLimit === undefined ? {} : { nodeLimit }),
            graphVersion: graphVersion ?? undefined,
          },
          signal,
        }),
      );
      // No `snapshotId` is ever supplied here, so the server always takes
      // the project-graph branch and the response always matches
      // `GraphSubgraph`; the generated type is a union only because the
      // legacy fragment-scoped branch (unused by this client) returns a
      // different shape.
      return data as GraphSubgraph;
    },
    async neighbors(nodeId, { depth, nodeLimit, graphVersion }, signal) {
      return unwrap(
        http.POST("/api/v1/projects/{project_id}/knowledge/graph/neighbors", {
          params: { path: pathProject },
          body: {
            nodeId,
            ...(depth === undefined ? {} : { depth }),
            ...(nodeLimit === undefined ? {} : { nodeLimit }),
            graphVersion: graphVersion ?? undefined,
          },
          signal,
        }),
      );
    },
    async path(
      { sourceNodeId, targetNodeId, nodeLimit, graphVersion },
      signal,
    ) {
      const data = await unwrap(
        http.POST("/api/v1/projects/{project_id}/knowledge/graph/path", {
          params: { path: pathProject },
          body: {
            sourceNodeId,
            targetNodeId,
            ...(nodeLimit === undefined ? {} : { nodeLimit }),
            graphVersion: graphVersion ?? undefined,
          },
          signal,
        }),
      );
      // Same union narrowing as `query` above: no `snapshotId` is supplied,
      // so the response always matches `GraphSubgraph`.
      return data as GraphSubgraph;
    },
    async node(nodeId, graphVersion, signal) {
      const data = await unwrap(
        http.GET(
          "/api/v1/projects/{project_id}/knowledge/graph/nodes/{node_id}",
          {
            params: {
              path: { ...pathProject, node_id: nodeId },
              query: { graphVersion: graphVersion ?? undefined },
            },
            signal,
          },
        ),
      );
      // No `snapshotId` query param is supplied, so the server always takes
      // the project-graph node-detail branch; the generated type is a union
      // only because the legacy fragment-scoped branches (unused by this
      // client) return different shapes.
      return data as GraphNodeDetail;
    },
    async highlight(edgeIds, signal) {
      return unwrap(
        http.POST("/api/v1/projects/{project_id}/knowledge/graph/highlight", {
          params: { path: pathProject },
          body: { edgeIds },
          signal,
        }),
      );
    },
    async retryFragment(revisionId, idempotencyKey) {
      await unwrap(
        http.POST(
          "/api/v1/projects/{project_id}/knowledge/graph/fragments/{revision_id}/retry",
          {
            params: {
              path: { ...pathProject, revision_id: revisionId },
            },
            headers: { "Idempotency-Key": idempotencyKey },
          },
        ),
      );
    },
  };
}
