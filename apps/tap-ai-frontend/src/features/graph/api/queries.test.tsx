import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "../../../shared/testing/renderApp";
import {
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

describe("graph version guard", () => {
  it("invalidates every graph query once after a version conflict", async () => {
    let overviewCalls = 0;
    let projectCalls = 0;
    const fetcher = vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      if (url.pathname.endsWith("/knowledge/graph/project")) {
        projectCalls += 1;
        if (projectCalls === 1) {
          return jsonResponse({
            graphVersion: null,
            status: "READY",
            nodeCount: 1,
            edgeCount: 0,
            mergedAt: null,
            communities: [],
            extractingRevisionIds: [],
            partialRevisionIds: [],
          });
        }
        return jsonResponse({
          graphVersion: 3,
          status: "READY",
          nodeCount: 1,
          edgeCount: 0,
          mergedAt: null,
          communities: [],
          extractingRevisionIds: [],
          partialRevisionIds: [],
        });
      }
      if (url.pathname.endsWith("/knowledge/graph/overview")) {
        overviewCalls += 1;
        if (overviewCalls === 1) {
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

    const { result, rerender } = renderHook(
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

    // The conflict -> project refetch -> new overview-query chain settles the
    // underlying query cache correctly (verified directly below), but the
    // cascading updates land outside any React-initiated `act()` boundary,
    // so this render's own subscription lags behind. An explicit flush plus
    // a no-op `rerender()` forces React to reconcile with the now-settled
    // cache before asserting on `result.current`.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 300));
    });
    rerender();

    await waitFor(() =>
      expect(result.current.project.data?.graphVersion).toBe(3),
    );
    await waitFor(() =>
      expect(result.current.overview.data?.graphVersion).toBe(3),
    );

    expect(overviewCalls).toBe(2);
    expect(projectCalls).toBe(2);
  });
});
