import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "../../../shared/testing/renderApp";
import { GraphVersionConflictError } from "./client";
import {
  graphKeys,
  useGraphHighlight,
  useGraphOverview,
  useGraphProject,
  useGraphVersionGuard,
} from "./queries";

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function versionMismatchResponse(): Response {
  return new Response(
    JSON.stringify({
      type: "https://tap.example/problems/graph-version-mismatch",
      title: "Graph version mismatch",
      status: 409,
      detail: "Current graph version is 3.",
      failureStage: "graph",
      retryable: false,
    }),
    { status: 409, headers: { "content-type": "application/problem+json" } },
  );
}

function projectView(graphVersion: number | null) {
  return {
    graphVersion,
    status: "READY" as const,
    nodeCount: 1,
    edgeCount: 0,
    mergedAt: null,
    communities: [],
    extractingRevisionIds: [],
    partialRevisionIds: [],
  };
}

describe("graph version guard", () => {
  it("re-keys a pinned query to the refreshed version after a 409, fetching the pinned query exactly twice", async () => {
    let overviewCalls = 0;
    let projectCalls = 0;
    const fetcher = vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      if (url.pathname.endsWith("/knowledge/graph/project")) {
        projectCalls += 1;
        // The project starts at v2 (pre-seeded into the cache below, no
        // network call needed for that); the only network call the project
        // query makes in this test is the guard's refetch, which observes
        // that the graph has since moved on to v3.
        return jsonResponse(projectView(3));
      }
      if (url.pathname.endsWith("/knowledge/graph/overview")) {
        overviewCalls += 1;
        // The overview query is pinned to the project's current version
        // (v2) on its first request; that pin is stale by the time it
        // lands, so the server 409s. The re-keyed request (v3, issued once
        // the guard's refetch resolves) succeeds.
        if (url.searchParams.get("graphVersion") === "2") {
          return versionMismatchResponse();
        }
        return jsonResponse({
          graphVersion: 3,
          nodes: [],
          edges: [],
          evidence: [],
        });
      }
      throw new Error(`Unexpected request to ${url.pathname}`);
    });
    vi.stubGlobal("fetch", fetcher);

    const queryClient = createTestQueryClient();
    const projectId = "project-1";

    // Seed the project query at v2 synchronously, so the very first render
    // already has a pinned version (no unpinned/null request in between) —
    // the realistic "project is already at v2" starting point.
    queryClient.setQueryData(graphKeys.project(projectId), projectView(2));

    const { result } = renderHook(
      () => {
        const project = useGraphProject(projectId);
        const graphVersion = project.data?.graphVersion ?? null;
        const overview = useGraphOverview(projectId, graphVersion, {
          sourceRevisionIds: [],
          communityIds: [],
          nodeLimit: 150,
        });
        useGraphVersionGuard(projectId, [project.error, overview.error]);
        return { project, overview };
      },
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            {children}
          </QueryClientProvider>
        ),
      },
    );

    // The underlying query cache settles correctly and promptly: the
    // pinned (v2) overview query 409s once, the guard refetches `GET
    // /project` to learn the current version (v3), and the re-keyed
    // overview query then succeeds. Asserted by polling the cache directly
    // with `waitFor` (condition-based, no fixed sleep).
    await waitFor(() =>
      expect(
        queryClient.getQueryState(graphKeys.project(projectId))?.data,
      ).toMatchObject({ graphVersion: 3 }),
    );
    await waitFor(() => {
      const overviewKey = graphKeys.overview(projectId, 3, [], [], 150);
      expect(queryClient.getQueryState(overviewKey)?.status).toBe("success");
    });

    expect(overviewCalls).toBe(2);
    expect(projectCalls).toBe(1);

    // Sanity: the hook's own return value eventually reflects the same
    // settled cache (confirms the guard doesn't leave the UI stuck even
    // though the assertions above already proved the data is correct).
    await waitFor(() =>
      expect(result.current.overview.data?.graphVersion).toBe(3),
    );
  });

  it("invalidates the whole graph query prefix, including a version-less cached query, exactly once per conflict", async () => {
    let projectRefetches = 0;
    const fetcher = vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      if (url.pathname.endsWith("/knowledge/graph/project")) {
        projectRefetches += 1;
        return jsonResponse(projectView(3));
      }
      if (url.pathname.endsWith("/knowledge/graph/highlight")) {
        return jsonResponse({
          graphVersion: 2,
          nodes: [],
          edges: [],
          evidence: [],
        });
      }
      throw new Error(`Unexpected request to ${url.pathname}`);
    });
    vi.stubGlobal("fetch", fetcher);

    const queryClient = createTestQueryClient();
    const projectId = "project-1";
    const edgeIds = ["edge-1"];
    const highlightKey = graphKeys.highlight(projectId, edgeIds);

    // Seed both queries as already settled (`staleTime: Infinity` for this
    // client only, so mounting their real `useQuery` observers below does
    // not itself trigger a refetch-on-mount) — a version-less cached query
    // (highlight never pins a `graphVersion`) and the project, both fresh
    // *before* any conflict occurs.
    queryClient.setQueryDefaults(["graph", projectId], {
      staleTime: Number.POSITIVE_INFINITY,
    });
    queryClient.setQueryData(highlightKey, {
      graphVersion: 2,
      nodes: [],
      edges: [],
      evidence: [],
    });
    queryClient.setQueryData(graphKeys.project(projectId), projectView(2));
    expect(queryClient.getQueryState(highlightKey)?.isInvalidated).toBe(false);

    let conflictErrors: unknown[] = [];
    const { rerender } = renderHook(
      () => {
        // Mounted so the project/highlight queries have real observers
        // (and queryFns) for `refetchQueries`/`invalidateQueries` to act
        // on — a cache entry seeded only via `setQueryData` has none.
        useGraphProject(projectId);
        useGraphHighlight(projectId, edgeIds);
        useGraphVersionGuard(projectId, conflictErrors);
      },
      {
        wrapper: ({ children }) => (
          <QueryClientProvider client={queryClient}>
            {children}
          </QueryClientProvider>
        ),
      },
    );

    // Simulate a graph request elsewhere 409ing on a stale version.
    conflictErrors = [new GraphVersionConflictError()];
    rerender();

    await waitFor(() => expect(projectRefetches).toBe(1));
    await waitFor(() =>
      expect(
        queryClient.getQueryState(graphKeys.project(projectId))?.data,
      ).toMatchObject({ graphVersion: 3 }),
    );

    // The highlight query was never refetched (it has no pinned version to
    // re-key on), but the prefix invalidation still marks it stale.
    expect(queryClient.getQueryState(highlightKey)?.isInvalidated).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);

    // Re-rendering with the same (still-conflicting) errors must not
    // trigger a second refetch — dedup keys on the conflict generation,
    // not on how many renders observe it.
    rerender();
    rerender();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(projectRefetches).toBe(1);

    // A genuinely new conflict (clear, then conflict again) triggers
    // exactly one more refetch.
    conflictErrors = [];
    rerender();
    conflictErrors = [new GraphVersionConflictError()];
    rerender();
    await waitFor(() => expect(projectRefetches).toBe(2));
  });
});
