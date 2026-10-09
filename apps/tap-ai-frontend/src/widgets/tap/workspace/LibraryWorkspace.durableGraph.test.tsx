import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type {
  GraphProject,
  GraphSubgraph,
} from "../../../features/graph/model/graph";
import { GraphVersionConflictError } from "../../../features/graph/api/client";
import {
  useGraphHighlight,
  useGraphNode,
  useGraphOverview,
  useGraphProject,
  useGraphSearch,
} from "../../../features/graph/api/queries";
import type { GraphHighlightState } from "../../../features/graph/model/highlight";
import { WORKSPACE_COPY } from "./copy";
import { LibraryWorkspace } from "./LibraryWorkspace";

// `useGraphVersionGuard` is kept real (not stubbed) so the version-conflict
// test below can observe its actual `queryClient.refetchQueries` side
// effect — every other hook stays a plain mock, as in the rest of this
// file.
vi.mock("../../../features/graph/api/queries", async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import("../../../features/graph/api/queries")
    >();
  return {
    useGraphProject: vi.fn(),
    useGraphOverview: vi.fn(),
    useGraphSearch: vi.fn(),
    useGraphNode: vi.fn(),
    useGraphHighlight: vi.fn(),
    useGraphVersionGuard: actual.useGraphVersionGuard,
  };
});

function queryResult<T>(
  data: T | undefined,
  overrides: Partial<{
    isPending: boolean;
    isError: boolean;
    isSuccess: boolean;
    isFetching: boolean;
    error: unknown;
    dataUpdatedAt: number;
    errorUpdatedAt: number;
    refetch: () => void;
  }> = {},
) {
  return {
    data,
    isPending: overrides.isPending ?? false,
    isError: overrides.isError ?? false,
    isSuccess: overrides.isSuccess ?? data !== undefined,
    isFetching: overrides.isFetching ?? false,
    error: overrides.error,
    dataUpdatedAt: overrides.dataUpdatedAt ?? 0,
    errorUpdatedAt: overrides.errorUpdatedAt ?? 0,
    refetch: overrides.refetch ?? vi.fn(),
  } as never;
}

function buildProject(overrides: Partial<GraphProject> = {}): GraphProject {
  return {
    graphVersion: 1,
    status: "READY",
    nodeCount: 8,
    edgeCount: 4,
    mergedAt: "2026-01-01T00:00:00Z",
    communities: [
      { communityId: "underwriting", label: "Underwriting", size: 5 },
      { communityId: "claims", label: "Claims", size: 3 },
    ],
    extractingRevisionIds: [],
    partialRevisionIds: [],
    ...overrides,
  } as GraphProject;
}

function buildNodes(
  count: number,
  communityId: string,
  prefix = "n",
): GraphSubgraph["nodes"] {
  return Array.from({ length: count }, (_, index) => ({
    nodeId: `${prefix}${index}`,
    label: `${prefix}${index}`,
    nodeType: "CONCEPT",
    canonicalKey: `${prefix}${index}`,
    degree: 1,
    communityId,
    aliases: [],
  }));
}

function buildOverview(
  nodes: GraphSubgraph["nodes"],
  edges: GraphSubgraph["edges"] = [],
): GraphSubgraph {
  return { graphVersion: 1, nodes, edges, evidence: [] };
}

function renderLibrary(overrides: {
  project?: GraphProject | null;
  projectPending?: boolean;
  projectError?: boolean;
  // `GraphOverview` reads this (`projectQuery.dataUpdatedAt`) against the
  // node query's own `errorUpdatedAt` to decide whether a lingering
  // version conflict is still "waiting for the project's refetch" or has
  // settled (the project updated *after* the conflict, which persists
  // anyway) and should surface as an error instead of loading forever.
  // Defaults to `0`, i.e. "never updated" — older than any conflict a test
  // gives an explicit `errorUpdatedAt` for, so the conflict reads as
  // unsettled/loading unless a test raises this above that value.
  projectDataUpdatedAt?: number;
  overview?: GraphSubgraph;
  search?: GraphSubgraph;
  locale?: "en" | "zh";
  sources?: Parameters<typeof LibraryWorkspace>[0]["sources"];
  publishedSources?: Parameters<typeof LibraryWorkspace>[0]["publishedSources"];
  publishedSourcesLoading?: boolean;
  loadState?: "loading" | "loaded" | "error";
  // Lets a test give `useGraphNode` a per-`nodeId` response (e.g. the
  // selected node's own detail vs. a relation neighbor's) instead of the
  // single static fixture every other test uses.
  nodeImplementation?: (
    ...args: Parameters<typeof useGraphNode>
  ) => ReturnType<typeof queryResult>;
  queryClient?: QueryClient;
  highlight?: GraphHighlightState | null;
  onClearGraphHighlight?: () => void;
  highlightResponse?: GraphSubgraph;
  highlightPending?: boolean;
  highlightError?: boolean;
  highlightFetching?: boolean;
  highlightRefetch?: () => void;
}) {
  vi.mocked(useGraphProject).mockReturnValue(
    queryResult(overrides.project ?? undefined, {
      isPending: overrides.projectPending ?? false,
      isError: overrides.projectError ?? false,
      dataUpdatedAt: overrides.projectDataUpdatedAt ?? 0,
    }),
  );
  vi.mocked(useGraphOverview).mockImplementation(() =>
    queryResult(overrides.overview),
  );
  vi.mocked(useGraphSearch).mockImplementation(() =>
    queryResult(overrides.search),
  );
  if (overrides.nodeImplementation) {
    vi.mocked(useGraphNode).mockImplementation(overrides.nodeImplementation);
  } else {
    vi.mocked(useGraphNode).mockReturnValue(queryResult(undefined));
  }
  vi.mocked(useGraphHighlight).mockReturnValue(
    queryResult(overrides.highlightResponse, {
      isPending: overrides.highlightPending ?? false,
      isError: overrides.highlightError ?? false,
      isFetching: overrides.highlightFetching ?? false,
      refetch: overrides.highlightRefetch,
    }),
  );

  const copy = WORKSPACE_COPY[overrides.locale ?? "en"];
  const queryClient =
    overrides.queryClient ??
    new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // A function, not a hoisted constant: React bails out of re-rendering a
  // subtree whose element is the exact same object reference as last time
  // (no prop identity change at any level), so `rerenderSame` below must
  // build a *fresh* element on every call for a test that swaps a mocked
  // hook's return value and expects the resulting update to actually
  // reach `GraphOverview`.
  const buildElement = () => (
    <QueryClientProvider client={queryClient}>
      <LibraryWorkspace
        copy={copy}
        locale={overrides.locale ?? "en"}
        graphProjectId="tapper-demo"
        sources={
          overrides.sources ?? [
            {
              id: "src_a",
              name: "Underwriting rules",
              type: "Markdown",
              status: "ready",
              origin: "knowledge-base",
              description: "Published source",
            },
          ]
        }
        publishedSources={
          overrides.publishedSources ?? [
            { sourceId: "src_a", revisionId: "rev_src_a" },
          ]
        }
        publishedSourcesLoading={overrides.publishedSourcesLoading ?? false}
        loadState={overrides.loadState}
        graphHighlight={overrides.highlight ?? null}
        onClearGraphHighlight={overrides.onClearGraphHighlight}
      />
    </QueryClientProvider>
  );
  const result = render(buildElement());
  const rerenderSame = () => result.rerender(buildElement());
  return { ...result, queryClient, rerenderSame };
}

it("renders communities from the project graph and colors nodes by community", async () => {
  const project = buildProject();
  const nodes = [
    ...buildNodes(1, "underwriting", "uw"),
    ...buildNodes(1, "claims", "cl"),
  ];
  renderLibrary({ project, overview: buildOverview(nodes) });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(
    screen.getByRole("checkbox", { name: "Underwriting · 5 nodes" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("combobox", { name: "Graph source" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/Published source graph/i)).not.toBeInTheDocument();
  const uwNode = screen.getByRole("button", { name: /Underwriting/ });
  expect(uwNode).toBeVisible();
  // "Underwriting" (size 5) is the largest community, so it gets the first
  // palette color (features/graph/model/palette.ts's GRAPH_PALETTE[0]).
  expect(uwNode.style.getPropertyValue("--tap-community-color")).toBe(
    "#2563eb",
  );
});

it("shows an empty state for a project without communities", async () => {
  const project = buildProject({ nodeCount: 0, communities: [] });
  renderLibrary({ project });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(screen.getByText("No knowledge graph yet.")).toBeVisible();
  expect(screen.getByText(/built automatically/i)).toBeVisible();
  expect(screen.queryByRole("group")).not.toBeInTheDocument();
});

it("loads more nodes when the overview is truncated", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(150, "underwriting")),
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  await userEvent.click(screen.getByRole("button", { name: "Load more" }));

  const lastCall = vi.mocked(useGraphOverview).mock.calls.at(-1);
  expect(lastCall?.[2]).toMatchObject({ nodeLimit: 300 });
});

it("passes the filtered published sources to the overview", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    sources: [
      {
        id: "src_a",
        name: "Underwriting rules",
        type: "Markdown",
        status: "ready",
        origin: "knowledge-base",
        description: "Published source",
      },
      {
        id: "src_b",
        name: "Failed upload",
        type: "Markdown",
        status: "failed",
        origin: "knowledge-base",
        description: "Failed source",
      },
    ],
    publishedSources: [
      { sourceId: "src_a", revisionId: "rev_src_a" },
      { sourceId: "src_b", revisionId: "rev_src_b" },
    ],
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Status" }),
    "ready",
  );
  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  const lastCall = vi.mocked(useGraphOverview).mock.calls.at(-1);
  expect(lastCall?.[2]).toMatchObject({ sourceRevisionIds: ["rev_src_a"] });
});

it("shows extraction footer counts", async () => {
  const project = buildProject({
    extractingRevisionIds: ["rev_src_a", "rev_src_b"],
    partialRevisionIds: ["rev_src_c"],
  });
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    sources: [
      {
        id: "src_a",
        name: "A",
        type: "Markdown",
        status: "ready",
        origin: "knowledge-base",
        description: "",
      },
      {
        id: "src_b",
        name: "B",
        type: "Markdown",
        status: "ready",
        origin: "knowledge-base",
        description: "",
      },
      {
        id: "src_c",
        name: "C",
        type: "Markdown",
        status: "ready",
        origin: "knowledge-base",
        description: "",
      },
    ],
    publishedSources: [
      { sourceId: "src_a", revisionId: "rev_src_a" },
      { sourceId: "src_b", revisionId: "rev_src_b" },
      { sourceId: "src_c", revisionId: "rev_src_c" },
    ],
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  // The footer must render inside the community column (`CommunityList`'s
  // own `footer` slot), not as an unplaced child of the 3-column canvas
  // grid, which would otherwise land it in the canvas or inspector cell.
  const communityRegion = screen.getByRole("complementary", {
    name: "Topic groups",
  });
  expect(
    within(communityRegion).getByText("2 sources still extracting"),
  ).toBeVisible();
  expect(within(communityRegion).getByText("1 partially failed")).toBeVisible();
});

it("uses the singular form of the extraction footer for exactly one source", async () => {
  const project = buildProject({
    extractingRevisionIds: ["rev_src_a"],
    partialRevisionIds: [],
  });
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    sources: [
      {
        id: "src_a",
        name: "A",
        type: "Markdown",
        status: "ready",
        origin: "knowledge-base",
        description: "",
      },
    ],
    publishedSources: [{ sourceId: "src_a", revisionId: "rev_src_a" }],
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  const communityRegion = screen.getByRole("complementary", {
    name: "Topic groups",
  });
  expect(
    within(communityRegion).getByText("1 source still extracting"),
  ).toBeVisible();
});

it("renders Chinese overview copy", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(150, "underwriting")),
    locale: "zh",
  });

  await userEvent.click(screen.getByRole("tab", { name: "知识图谱" }));

  expect(screen.getByText(/项目知识总览/)).toBeVisible();
  expect(screen.getByRole("button", { name: "加载更多" })).toBeVisible();
  expect(
    within(screen.getByRole("button", { name: "加载更多" })),
  ).toBeDefined();
});

it("shows a loading message while the project graph is loading", async () => {
  renderLibrary({ project: undefined, projectPending: true });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(screen.getByText("Loading the knowledge graph…")).toBeVisible();
});

it("shows an unavailable message when the project graph query fails", async () => {
  renderLibrary({ project: undefined, projectError: true });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(
    screen.getByText(
      "The knowledge graph is temporarily unavailable. Try again.",
    ),
  ).toBeVisible();
});

it("shows a loading state while published sources are still loading", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    publishedSourcesLoading: true,
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(screen.getByText("Loading sources")).toBeVisible();
  expect(vi.mocked(useGraphOverview).mock.calls.at(-1)?.[0]).toBeNull();
});

it("shows a no-match state when Library filters match no published source", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    sources: [
      {
        id: "src_a",
        name: "Underwriting rules",
        type: "Markdown",
        status: "ready",
        origin: "knowledge-base",
        description: "Published source",
      },
    ],
    publishedSources: [{ sourceId: "src_a", revisionId: "rev_src_a" }],
  });

  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Status" }),
    "failed",
  );
  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(screen.getByText("No matching sources")).toBeVisible();
  expect(vi.mocked(useGraphOverview).mock.calls.at(-1)?.[0]).toBeNull();
});

it("shows a loading state (not no-match) when a facet is active and the source list is still loading", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    sources: [],
    publishedSources: [],
    loadState: "loading",
  });

  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Status" }),
    "failed",
  );
  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(screen.getByText("Loading sources")).toBeVisible();
  expect(screen.queryByText("No matching sources")).not.toBeInTheDocument();
  expect(vi.mocked(useGraphOverview).mock.calls.at(-1)?.[0]).toBeNull();
});

it("shows an unavailable state (not no-match) when a facet is active and the source list failed to load", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    sources: [],
    publishedSources: [],
    loadState: "error",
  });

  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Status" }),
    "failed",
  );
  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(
    screen.getByText(
      "The knowledge graph is temporarily unavailable. Try again.",
    ),
  ).toBeVisible();
  expect(screen.queryByText("No matching sources")).not.toBeInTheDocument();
  expect(vi.mocked(useGraphOverview).mock.calls.at(-1)?.[0]).toBeNull();
});

it("keeps the graph scoped to type/status facets, not the free-text search box", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    sources: [
      {
        id: "src_a",
        name: "Underwriting rules",
        type: "Markdown",
        status: "ready",
        origin: "knowledge-base",
        description: "Published source",
      },
      {
        id: "src_b",
        name: "Claims control",
        type: "Markdown",
        status: "ready",
        origin: "knowledge-base",
        description: "Published source",
      },
    ],
    publishedSources: [
      { sourceId: "src_a", revisionId: "rev_src_a" },
      { sourceId: "src_b", revisionId: "rev_src_b" },
    ],
  });

  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "Search library" }),
    "Underwriting",
  );
  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  // No type/status facet is active, so the scope is unfiltered -- sent as
  // an empty array (the backend's "no filter" / whole-project meaning),
  // not every published source id (see `MAX_GRAPH_SOURCE_REVISIONS`: a
  // project with more than 50 published sources would otherwise 422).
  const lastCall = vi.mocked(useGraphOverview).mock.calls.at(-1);
  expect(lastCall?.[2]).toMatchObject({
    sourceRevisionIds: [],
  });
});

it("sends an empty sourceRevisionIds (whole-project scope) when no source facet is active", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  const lastCall = vi.mocked(useGraphOverview).mock.calls.at(-1);
  expect(lastCall?.[2]).toMatchObject({ sourceRevisionIds: [] });
});

it("caps a source facet matching more than 50 sources and shows a notice", async () => {
  const project = buildProject();
  const sources = Array.from({ length: 60 }, (_, index) => ({
    id: `src_${index}`,
    name: `Source ${index}`,
    type: "Markdown",
    status: "ready" as const,
    origin: "knowledge-base" as const,
    description: "",
  }));
  const publishedSources = sources.map((source) => ({
    sourceId: source.id,
    revisionId: `rev_${source.id}`,
  }));
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    sources,
    publishedSources,
  });

  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Status" }),
    "ready",
  );
  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  const lastCall = vi.mocked(useGraphOverview).mock.calls.at(-1);
  const sentIds = (
    lastCall?.[2] as { sourceRevisionIds: readonly string[] } | undefined
  )?.sourceRevisionIds;
  expect(sentIds).toHaveLength(50);
  expect(
    screen.getByText(
      "Showing the graph for the first 50 of 60 matching sources.",
    ),
  ).toBeVisible();
});

it("renders localized node type labels in the Chinese search results", async () => {
  const project = buildProject();
  const nodes = buildNodes(1, "underwriting", "uw");
  renderLibrary({
    project,
    overview: buildOverview(nodes),
    search: buildOverview(nodes),
    locale: "zh",
  });

  await userEvent.click(screen.getByRole("tab", { name: "知识图谱" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "搜索知识库" }),
    "uw0",
  );

  const results = screen.getByRole("region", { name: "搜索结果" });
  expect(within(results).getByText(/概念/)).toBeVisible();
  expect(within(results).queryByText(/CONCEPT/)).not.toBeInTheDocument();
});

it("matches nodes by their localized node type label when searching", async () => {
  const project = buildProject();
  const nodes = buildNodes(1, "underwriting", "uw");
  renderLibrary({
    project,
    overview: buildOverview(nodes),
    search: buildOverview(nodes),
    locale: "zh",
  });

  await userEvent.click(screen.getByRole("tab", { name: "知识图谱" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "搜索知识库" }),
    "概念",
  );

  const results = screen.getByRole("region", { name: "搜索结果" });
  expect(within(results).getByText("uw0")).toBeVisible();
});

it("keeps the node detail panel open for a relation neighbor outside the drawn view", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    nodeImplementation: ((_projectId, _graphVersion, nodeId) => {
      if (nodeId === "n0") {
        return queryResult({
          graphVersion: 1,
          node: {
            nodeId: "n0",
            label: "n0",
            nodeType: "CONCEPT",
            canonicalKey: "n0",
            degree: 1,
            communityId: "underwriting",
            aliases: [],
          },
          community: {
            communityId: "underwriting",
            label: "Underwriting",
            size: 5,
          },
          sources: [],
          relations: [
            {
              relationType: "REQUIRES",
              edges: [
                {
                  edgeId: "e1",
                  sourceNodeId: "n0",
                  targetNodeId: "ghost",
                  relationType: "REQUIRES",
                  relationLabel: "Requires",
                  confidence: 0.9,
                  origin: "EXTRACTED",
                },
              ],
            },
          ],
          neighbors: [
            {
              nodeId: "ghost",
              label: "Ghost node",
              nodeType: "CONCEPT",
              canonicalKey: "ghost",
              degree: 1,
              aliases: [],
            },
          ],
        });
      }
      if (nodeId === "ghost") {
        return queryResult({
          graphVersion: 1,
          node: {
            nodeId: "ghost",
            label: "Ghost node",
            nodeType: "CONCEPT",
            canonicalKey: "ghost",
            degree: 1,
            communityId: null,
            aliases: [],
          },
          community: null,
          sources: [],
          relations: [],
          neighbors: [],
        });
      }
      return queryResult(undefined);
    }) as Parameters<typeof renderLibrary>[0]["nodeImplementation"],
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  await userEvent.click(screen.getByRole("button", { name: /n0/ }));
  await userEvent.click(screen.getByRole("button", { name: /Ghost node/ }));

  expect(
    screen.getByRole("heading", { name: "Ghost node", level: 3 }),
  ).toBeVisible();
  expect(
    screen.queryByText("Select a node to inspect its relationships."),
  ).not.toBeInTheDocument();
});

it("refetches the project without showing an error when a node 409s on a stale version", async () => {
  const project = buildProject();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const refetchSpy = vi.spyOn(queryClient, "refetchQueries");
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    // The project's own last update (default `dataUpdatedAt: 0`, see
    // `renderLibrary`) predates this conflict's `errorUpdatedAt: 1000` —
    // i.e. the guard's `GET /project` refetch (asserted via `refetchSpy`
    // below) has not resolved since the conflict began, so the node query
    // reads as "still loading", not a settled, stuck conflict.
    nodeImplementation: ((_projectId, _graphVersion, nodeId) =>
      nodeId === null
        ? queryResult(undefined)
        : queryResult(undefined, {
            isError: true,
            error: new GraphVersionConflictError(),
            errorUpdatedAt: 1000,
          })) as Parameters<typeof renderLibrary>[0]["nodeImplementation"],
    queryClient,
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  await userEvent.click(screen.getByRole("button", { name: /n0/ }));

  expect(screen.getByText("Loading node details…")).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(refetchSpy).toHaveBeenCalledWith(
    expect.objectContaining({
      queryKey: ["graph", "tapper-demo", "project"],
      exact: true,
    }),
  );
});

it("never leaves the panel stuck on loading when a retry 409s again after the first conflict settled", async () => {
  const project = buildProject();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const refetchSpy = vi.spyOn(queryClient, "refetchQueries");
  const refetchNode = vi.fn();
  // A stable instance per conflict "phase" — the mocked `useGraphNode`
  // must keep returning the *same* error object reference across renders
  // within a phase (exactly like a real cached react-query error would),
  // not a fresh `new GraphVersionConflictError()` on every call, or the
  // reference-based guard (round 2, item 1's fix) would misread every
  // render as a brand-new conflict.
  const firstConflictError = new GraphVersionConflictError();
  const secondConflictError = new GraphVersionConflictError();

  // Step 1: a first conflict, not yet settled.
  const { rerenderSame } = renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    projectDataUpdatedAt: 0,
    nodeImplementation: ((_projectId, _graphVersion, nodeId) =>
      nodeId === null
        ? queryResult(undefined)
        : queryResult(undefined, {
            isError: true,
            error: firstConflictError,
            errorUpdatedAt: 1000,
            refetch: refetchNode,
          })) as Parameters<typeof renderLibrary>[0]["nodeImplementation"],
    queryClient,
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  await userEvent.click(screen.getByRole("button", { name: /n0/ }));
  expect(screen.getByText("Loading node details…")).toBeVisible();

  // Step 2: the guard's project refetch settles (still the stale version,
  // so the conflict persists) — the panel must show error + a working
  // Retry, not keep loading forever.
  vi.mocked(useGraphProject).mockReturnValue(
    queryResult(project, { dataUpdatedAt: 2000 }),
  );
  rerenderSame();
  expect(
    screen.getByText("Node details are unavailable. Try again."),
  ).toBeVisible();

  // Step 3: the user clicks Retry; the same query re-fetches and 409s
  // again with a brand-new conflict error instance (a later
  // `errorUpdatedAt`). The query's `isError` never passes through `false`
  // in between, so this is exactly the scenario the `useGraphVersionGuard`
  // fix (round 2, item 1) targets.
  await userEvent.click(screen.getByRole("button", { name: "Retry loading" }));
  expect(refetchNode).toHaveBeenCalled();
  vi.mocked(useGraphNode).mockImplementation(
    (_projectId, _graphVersion, nodeId) =>
      nodeId === null
        ? queryResult(undefined)
        : queryResult(undefined, {
            isError: true,
            error: secondConflictError,
            errorUpdatedAt: 3000,
            refetch: refetchNode,
          }),
  );
  rerenderSame();

  // Immediately after the second, distinct conflict, the project hasn't
  // refetched again yet — this is the same not-yet-settled "loading" state
  // as step 1, not a stuck error.
  expect(screen.getByText("Loading node details…")).toBeVisible();
  // The guard fired again for this second, distinct conflict (not just
  // once, ever) — this is the actual fix: without it, the panel would
  // never leave this "loading" state because nothing would ever refetch
  // the project to let the conflict re-settle.
  expect(refetchSpy).toHaveBeenCalledTimes(2);

  // Step 4: the project refetches again and settles after the second
  // conflict — the panel must show error + retry again, never stuck.
  vi.mocked(useGraphProject).mockReturnValue(
    queryResult(project, { dataUpdatedAt: 4000 }),
  );
  rerenderSame();
  expect(
    screen.getByText("Node details are unavailable. Try again."),
  ).toBeVisible();
  expect(screen.queryByText("Loading node details…")).not.toBeInTheDocument();
});

function buildHighlightEdge(
  overrides: Partial<GraphSubgraph["edges"][number]> = {},
): GraphSubgraph["edges"][number] {
  return {
    edgeId: "e1",
    sourceNodeId: "uw1",
    targetNodeId: "hd1",
    relationType: "REQUIRES",
    relationLabel: "requires",
    confidence: 0.9,
    origin: "EXTRACTED",
    ...overrides,
  };
}

function buildHighlightNode(
  overrides: Partial<GraphSubgraph["nodes"][number]> = {},
): GraphSubgraph["nodes"][number] {
  return {
    nodeId: "uw1",
    label: "Underwriting review",
    nodeType: "PROCESS",
    canonicalKey: "uw1",
    degree: 2,
    communityId: "underwriting",
    aliases: [],
    ...overrides,
  };
}

const HIGHLIGHT_GRAPH_NODES: GraphSubgraph["nodes"] = [
  buildHighlightNode(),
  buildHighlightNode({ nodeId: "hd1", label: "Health disclosure" }),
  buildHighlightNode({ nodeId: "me1", label: "Medical exam" }),
  buildHighlightNode({ nodeId: "ot1", label: "Other node" }),
];
const HIGHLIGHT_GRAPH_EDGES: GraphSubgraph["edges"] = [
  buildHighlightEdge(),
  buildHighlightEdge({
    edgeId: "e2",
    sourceNodeId: "hd1",
    targetNodeId: "me1",
    relationType: "TRIGGERS",
    relationLabel: "triggers",
  }),
];
const HIGHLIGHT_STATE: GraphHighlightState = {
  edgeIds: ["e1", "e2"],
  graphVersion: "1",
  turnId: "turn_1",
  projectId: "tapper-demo",
};

it("highlights cited edges, dims the rest and lists the path as text", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
    highlightResponse: buildOverview(
      HIGHLIGHT_GRAPH_NODES.slice(0, 3),
      HIGHLIGHT_GRAPH_EDGES,
    ),
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(
    screen.getByRole("button", { name: /Underwriting review/ }),
  ).toHaveAttribute("data-highlighted", "true");
  expect(
    screen.getByRole("button", { name: /Health disclosure/ }),
  ).toHaveAttribute("data-highlighted", "true");
  expect(screen.getByRole("button", { name: /Other node/ })).toHaveAttribute(
    "data-dimmed",
    "true",
  );

  const region = screen.getByRole("region", { name: "Highlighted path" });
  expect(
    within(region).getByText(
      "Underwriting review —requires→ Health disclosure",
    ),
  ).toBeVisible();
});

it("excludes a 1-hop context edge the highlight response adds beyond what was requested", async () => {
  // `POST /highlight` returns the requested edges plus their 1-hop graph
  // context -- a context edge (here `hd1 -> ot1`, not in `HIGHLIGHT_STATE`'s
  // requested `edgeIds`) must stay normal (dimmed) context: it is neither
  // data-highlighted on the canvas nor listed in "Relations cited by the
  // answer".
  const project = buildProject();
  const contextEdge = buildHighlightEdge({
    edgeId: "ctx1",
    sourceNodeId: "hd1",
    targetNodeId: "ot1",
    relationType: "PRECEDES",
    relationLabel: "precedes",
  });
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
    highlightResponse: buildOverview(HIGHLIGHT_GRAPH_NODES, [
      ...HIGHLIGHT_GRAPH_EDGES,
      contextEdge,
    ]),
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(screen.getByRole("button", { name: /Other node/ })).toHaveAttribute(
    "data-dimmed",
    "true",
  );
  expect(
    screen.queryByRole("button", { name: /Other node/ }),
  ).not.toHaveAttribute("data-highlighted", "true");

  const region = screen.getByRole("region", { name: "Highlighted path" });
  const items = within(region).getAllByRole("listitem");
  expect(items).toHaveLength(2);
  expect(within(region).queryByText(/Other node/)).not.toBeInTheDocument();
});

it("renders a version-updated notice when highlight omits requested edges", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
    // Only `e1` of the two requested edge ids comes back — `e2` no longer
    // exists in the current graph.
    highlightResponse: buildOverview(HIGHLIGHT_GRAPH_NODES.slice(0, 2), [
      HIGHLIGHT_GRAPH_EDGES[0]!,
    ]),
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(
    screen.getByText(
      "The graph has been updated; some relations are no longer available.",
    ),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: /Underwriting review/ }),
  ).toHaveAttribute("data-highlighted", "true");
});

it("shows a loading state, not the version-updated notice, while the highlight request is pending", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
    highlightPending: true,
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(screen.getByText("Loading the highlighted path…")).toBeVisible();
  expect(
    screen.queryByText(
      "The graph has been updated; some relations are no longer available.",
    ),
  ).not.toBeInTheDocument();
  // No node is dimmed while the highlight is still pending — the canvas
  // must not read "pending" as "no edges matched".
  expect(
    screen.queryByRole("button", { name: /Other node/ }),
  ).not.toHaveAttribute("data-dimmed", "true");
});

it("shows an error with a working retry, not the version-updated notice, when the highlight request fails", async () => {
  const project = buildProject();
  const highlightRefetch = vi.fn();
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
    highlightError: true,
    highlightRefetch,
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(
    screen.getByText("The highlighted path is temporarily unavailable."),
  ).toBeVisible();
  expect(
    screen.queryByText(
      "The graph has been updated; some relations are no longer available.",
    ),
  ).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Retry loading" }));
  expect(highlightRefetch).toHaveBeenCalled();
});

it("shows the loading state, not the stale error text, while a highlight retry is in flight", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
    // A query that previously settled into `error` keeps `isError: true`
    // (and `isPending: false`) for the whole duration of a `refetch()` —
    // react query only updates `status` once the new attempt itself
    // resolves. `isFetching: true` is the only signal that a retry is
    // actually in flight right now.
    highlightError: true,
    highlightFetching: true,
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(screen.getByText("Loading the highlighted path…")).toBeVisible();
  expect(
    screen.queryByText("The highlighted path is temporarily unavailable."),
  ).not.toBeInTheDocument();
});

it("keeps showing the highlighted path and canvas highlight during a background refetch of already-successful data", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
    highlightResponse: buildOverview(
      HIGHLIGHT_GRAPH_NODES.slice(0, 3),
      HIGHLIGHT_GRAPH_EDGES,
    ),
    // React query sets `isFetching: true` during an ordinary background
    // refetch of data that already succeeded — `isError`/`isPending` stay
    // `false` throughout. This must not flicker the canvas highlight away
    // or flash a "Loading…" line over the still-valid, last-known-good
    // path (unlike the retry-after-error case above, where `isFetching`
    // alongside `isError` *does* mean "pending").
    highlightFetching: true,
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(
    screen.queryByText("Loading the highlighted path…"),
  ).not.toBeInTheDocument();
  const region = screen.getByRole("region", { name: "Highlighted path" });
  expect(
    within(region).getByText(
      "Underwriting review —requires→ Health disclosure",
    ),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: /Underwriting review/ }),
  ).toHaveAttribute("data-highlighted", "true");
});

it("defaults to the Knowledge Graph tab on mount when a highlight is already present", () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
  });

  // No click on either tab — a project is selected (which otherwise
  // defaults the Library to the Documents tab), but a pending highlight
  // must still land on Knowledge Graph.
  expect(screen.getByRole("tab", { name: "Knowledge Graph" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByRole("tab", { name: "Documents" })).toHaveAttribute(
    "aria-selected",
    "false",
  );
});

it("switches to the Knowledge Graph tab when a highlight arrives after mount, without clicking it", () => {
  const project = buildProject();
  const overrides: Parameters<typeof renderLibrary>[0] = {
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: null,
  };
  const { rerenderSame } = renderLibrary(overrides);

  // No highlight yet — the Library defaults to Documents (a project is
  // selected).
  expect(screen.getByRole("tab", { name: "Documents" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByRole("tab", { name: "Knowledge Graph" })).toHaveAttribute(
    "aria-selected",
    "false",
  );

  // A highlight arrives (e.g. the `useGraphHighlightState` hook in
  // `TapperWorkspace` resolving a same-tab push or a popstate navigation)
  // — the Library must switch tabs on its own, not wait for a click.
  overrides.highlight = HIGHLIGHT_STATE;
  rerenderSame();

  expect(screen.getByRole("tab", { name: "Knowledge Graph" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByRole("tab", { name: "Documents" })).toHaveAttribute(
    "aria-selected",
    "false",
  );
});

it("fits the viewport to the highlighted nodes", async () => {
  const project = buildProject();
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
    highlightResponse: buildOverview(
      HIGHLIGHT_GRAPH_NODES.slice(0, 3),
      HIGHLIGHT_GRAPH_EDGES,
    ),
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

  expect(
    screen.getByRole("status", { name: "Zoom level" }),
  ).not.toHaveTextContent("100%");
});

it("calls back to clear the highlight from the Highlighted path region", async () => {
  const project = buildProject();
  const onClearGraphHighlight = vi.fn();
  renderLibrary({
    project,
    overview: buildOverview(HIGHLIGHT_GRAPH_NODES, HIGHLIGHT_GRAPH_EDGES),
    highlight: HIGHLIGHT_STATE,
    highlightResponse: buildOverview(
      HIGHLIGHT_GRAPH_NODES.slice(0, 3),
      HIGHLIGHT_GRAPH_EDGES,
    ),
    onClearGraphHighlight,
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Back to overview" }),
  );

  expect(onClearGraphHighlight).toHaveBeenCalled();
});

function nodeDetailImplementation(): ReturnType<typeof queryResult> {
  return queryResult({
    graphVersion: 1,
    node: {
      nodeId: "n0",
      label: "n0",
      nodeType: "CONCEPT",
      canonicalKey: "n0",
      degree: 1,
      communityId: "underwriting",
      aliases: [],
    },
    community: {
      communityId: "underwriting",
      label: "Underwriting",
      size: 5,
    },
    sources: [],
    relations: [],
    neighbors: [],
  });
}

it("returns focus to the canvas region, not the document body, when the node detail panel closes on a graph version change and focus was inside it", async () => {
  const project = buildProject({ graphVersion: 1 });
  const { rerenderSame } = renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    nodeImplementation: nodeDetailImplementation as Parameters<
      typeof renderLibrary
    >[0]["nodeImplementation"],
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  await userEvent.click(screen.getByRole("button", { name: /n0/ }));
  expect(screen.getByRole("heading", { name: "n0", level: 3 })).toBeVisible();
  // Focus is inside the now-open panel (its own "Close node details"
  // button), not on the canvas node button that was clicked to open it.
  screen.getByRole("button", { name: "Close node details" }).focus();

  vi.mocked(useGraphProject).mockReturnValue(
    queryResult(buildProject({ graphVersion: 2 }), {}),
  );
  rerenderSame();

  expect(
    screen.queryByRole("heading", { name: "n0", level: 3 }),
  ).not.toBeInTheDocument();
  expect(document.body).not.toHaveFocus();
  expect(
    screen.getByRole("region", { name: "Knowledge graph canvas" }),
  ).toHaveFocus();
});

it("leaves focus in place on a graph version change when focus was outside the node detail panel (e.g. the search box)", async () => {
  const project = buildProject({ graphVersion: 1 });
  const { rerenderSame } = renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    nodeImplementation: nodeDetailImplementation as Parameters<
      typeof renderLibrary
    >[0]["nodeImplementation"],
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  await userEvent.click(screen.getByRole("button", { name: /n0/ }));
  expect(screen.getByRole("heading", { name: "n0", level: 3 })).toBeVisible();
  const searchBox = screen.getByRole("textbox", { name: "Search library" });
  searchBox.focus();
  expect(searchBox).toHaveFocus();

  vi.mocked(useGraphProject).mockReturnValue(
    queryResult(buildProject({ graphVersion: 2 }), {}),
  );
  rerenderSame();

  expect(
    screen.queryByRole("heading", { name: "n0", level: 3 }),
  ).not.toBeInTheDocument();
  expect(searchBox).toHaveFocus();
  expect(
    screen.queryByRole("region", { name: "Knowledge graph canvas" }),
  ).not.toHaveFocus();
});
