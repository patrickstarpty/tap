import { afterEach, describe, expect, it, vi } from "vitest";

import { createGraphClient, GraphVersionConflictError } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function problemResponse(detail: string, status = 409): Response {
  return new Response(
    JSON.stringify({
      type: "https://tap.example/problems/graph-version-mismatch",
      title: "Graph version mismatch",
      status,
      detail,
      failureStage: "graph",
      retryable: false,
    }),
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

function graphJobBusyResponse(): Response {
  return new Response(
    JSON.stringify({
      type: "https://tap.example/problems/graph-job-busy",
      title: "Graph job busy",
      status: 409,
      detail: "A graph job is already running for this project.",
      failureStage: "graph",
      retryable: false,
    }),
    { status: 409, headers: { "content-type": "application/problem+json" } },
  );
}

const subgraph = { graphVersion: 2, nodes: [], edges: [], evidence: [] };

describe("createGraphClient", () => {
  it("sends graphVersion with overview and never with highlight", async () => {
    const requests: Request[] = [];
    const fetcher = vi.fn(async (input: Request) => {
      requests.push(input);
      return jsonResponse(subgraph);
    });
    vi.stubGlobal("fetch", fetcher);

    const client = createGraphClient("project-1");
    await client.overview({
      sourceRevisionIds: ["rev-1"],
      communityIds: ["community-1"],
      nodeLimit: 150,
      graphVersion: 2,
    });
    await client.highlight(["edge-1", "edge-2"]);

    expect(requests).toHaveLength(2);
    const overviewUrl = new URL(requests[0]!.url);
    expect(overviewUrl.pathname).toBe(
      "/api/v1/projects/project-1/knowledge/graph/overview",
    );
    expect(overviewUrl.searchParams.get("graphVersion")).toBe("2");
    expect(overviewUrl.searchParams.get("nodeLimit")).toBe("150");

    const highlightRequest = requests[1]!;
    expect(highlightRequest.method).toBe("POST");
    const highlightBody = (await highlightRequest.json()) as Record<
      string,
      unknown
    >;
    expect(highlightBody).toEqual({ edgeIds: ["edge-1", "edge-2"] });
    expect(Object.keys(highlightBody)).not.toContain("graphVersion");
  });

  it("maps 409 to GraphVersionConflictError with no structured version", async () => {
    const fetcher = vi.fn(async () =>
      problemResponse("Current graph version is 3."),
    );
    vi.stubGlobal("fetch", fetcher);

    const client = createGraphClient("project-1");
    let caught: unknown;
    try {
      await client.overview({
        sourceRevisionIds: [],
        communityIds: [],
        nodeLimit: 150,
        graphVersion: 2,
      });
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(GraphVersionConflictError);
    expect((caught as Error).name).toBe("GraphVersionConflictError");
    expect(Object.prototype.hasOwnProperty.call(caught, "currentVersion")).toBe(
      false,
    );
  });

  it("does not map a graph-job-busy 409 to a version conflict", async () => {
    const fetcher = vi.fn(async () => graphJobBusyResponse());
    vi.stubGlobal("fetch", fetcher);

    const client = createGraphClient("project-1");
    let caught: unknown;
    try {
      await client.retryFragment("rev_x", "retry-intent-1");
    } catch (error) {
      caught = error;
    }
    expect(caught).not.toBeInstanceOf(GraphVersionConflictError);
    expect(caught).toBeInstanceOf(Error);
    expect((caught as Error).name).not.toBe("GraphVersionConflictError");
  });

  it("omits graphVersion when it is 0 (no version)", async () => {
    const requests: Request[] = [];
    const fetcher = vi.fn(async (input: Request) => {
      requests.push(input);
      return jsonResponse(subgraph);
    });
    vi.stubGlobal("fetch", fetcher);

    const client = createGraphClient("project-1");
    await client.overview({
      sourceRevisionIds: [],
      communityIds: [],
      nodeLimit: 150,
      graphVersion: 0,
    });

    const overviewUrl = new URL(requests[0]!.url);
    expect(overviewUrl.searchParams.has("graphVersion")).toBe(false);
  });

  it("retries a fragment with an idempotency key", async () => {
    const requests: Request[] = [];
    const fetcher = vi.fn(async (input: Request) => {
      requests.push(input);
      return jsonResponse({
        revisionId: "rev_x",
        jobStatus: "PENDING",
        requeuedBatches: 2,
      });
    });
    vi.stubGlobal("fetch", fetcher);

    const client = createGraphClient("project-1");
    await client.retryFragment("rev_x", "retry-intent-1");

    expect(requests).toHaveLength(1);
    const request = requests[0]!;
    expect(request.method).toBe("POST");
    expect(new URL(request.url).pathname).toBe(
      "/api/v1/projects/project-1/knowledge/graph/fragments/rev_x/retry",
    );
    expect(request.headers.get("Idempotency-Key")).toBe("retry-intent-1");
  });
});
