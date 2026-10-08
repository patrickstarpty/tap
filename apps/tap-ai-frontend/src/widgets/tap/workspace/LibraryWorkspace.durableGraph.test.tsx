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
  }> = {},
) {
  return {
    data,
    isPending: overrides.isPending ?? false,
    isError: overrides.isError ?? false,
    isSuccess: overrides.isSuccess ?? data !== undefined,
    isFetching: overrides.isFetching ?? false,
    error: overrides.error,
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
  // Mirrors `useGraphVersionGuard`'s `refetchQueries` call for `GET
  // /project` still being in flight — `GraphOverview` reads this (as
  // `!projectQuery.isFetching`) to decide whether a lingering version
  // conflict on the node query is still "waiting for the fresh version" or
  // has settled and should surface as an error instead of loading forever.
  projectFetching?: boolean;
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
}) {
  vi.mocked(useGraphProject).mockReturnValue(
    queryResult(overrides.project ?? undefined, {
      isPending: overrides.projectPending ?? false,
      isError: overrides.projectError ?? false,
      isFetching: overrides.projectFetching ?? false,
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
    queryResult(overrides.highlightResponse),
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

  const lastCall = vi.mocked(useGraphOverview).mock.calls.at(-1);
  expect(lastCall?.[2]).toMatchObject({
    sourceRevisionIds: ["rev_src_a", "rev_src_b"],
  });
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
    // The guard's own `GET /project` refetch (asserted via `refetchSpy`
    // below) is still in flight at this point — `projectFetching: true`
    // mirrors that so the node query's conflict reads as "still loading",
    // not a settled, stuck conflict.
    projectFetching: true,
    nodeImplementation: ((_projectId, _graphVersion, nodeId) =>
      nodeId === null
        ? queryResult(undefined)
        : queryResult(undefined, {
            isError: true,
            error: new GraphVersionConflictError(),
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

it("returns focus to the canvas region, not the document body, when the node detail panel closes on a graph version change", async () => {
  const project = buildProject({ graphVersion: 1 });
  const { rerenderSame } = renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting")),
    nodeImplementation: (() =>
      queryResult({
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
      })) as Parameters<typeof renderLibrary>[0]["nodeImplementation"],
  });

  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  await userEvent.click(screen.getByRole("button", { name: /n0/ }));
  expect(screen.getByRole("heading", { name: "n0", level: 3 })).toBeVisible();

  vi.mocked(useGraphProject).mockReturnValue(
    queryResult(buildProject({ graphVersion: 2 }), {}),
  );
  rerenderSame();

  expect(
    screen.queryByRole("heading", { name: "n0", level: 3 }),
  ).not.toBeInTheDocument();
  expect(document.body).not.toHaveFocus();
  expect(
    screen.getByRole("region", {
      name: "Drag to pan, use the controls to zoom, and select a node to inspect its relationships.",
    }),
  ).toHaveFocus();
});
