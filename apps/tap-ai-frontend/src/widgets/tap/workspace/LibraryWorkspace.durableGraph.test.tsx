import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type {
  GraphProject,
  GraphSubgraph,
} from "../../../features/graph/model/graph";
import {
  useGraphNode,
  useGraphOverview,
  useGraphProject,
  useGraphSearch,
} from "../../../features/graph/api/queries";
import { WORKSPACE_COPY } from "./copy";
import { LibraryWorkspace } from "./LibraryWorkspace";

vi.mock("../../../features/graph/api/queries", () => ({
  useGraphProject: vi.fn(),
  useGraphOverview: vi.fn(),
  useGraphSearch: vi.fn(),
  useGraphNode: vi.fn(),
  useGraphHighlight: vi.fn(() => ({ data: undefined, isPending: false })),
}));

function queryResult<T>(
  data: T | undefined,
  overrides: Partial<{
    isPending: boolean;
    isError: boolean;
    isSuccess: boolean;
  }> = {},
) {
  return {
    data,
    isPending: overrides.isPending ?? false,
    isError: overrides.isError ?? false,
    isSuccess: overrides.isSuccess ?? data !== undefined,
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
  overview?: GraphSubgraph;
  search?: GraphSubgraph;
  locale?: "en" | "zh";
  sources?: Parameters<typeof LibraryWorkspace>[0]["sources"];
  publishedSources?: Parameters<typeof LibraryWorkspace>[0]["publishedSources"];
  publishedSourcesLoading?: boolean;
}) {
  vi.mocked(useGraphProject).mockReturnValue(
    queryResult(overrides.project ?? undefined, {
      isPending: overrides.projectPending ?? false,
      isError: overrides.projectError ?? false,
    }),
  );
  vi.mocked(useGraphOverview).mockImplementation(() =>
    queryResult(overrides.overview),
  );
  vi.mocked(useGraphSearch).mockImplementation(() =>
    queryResult(overrides.search),
  );
  vi.mocked(useGraphNode).mockReturnValue(queryResult(undefined));

  const copy = WORKSPACE_COPY[overrides.locale ?? "en"];
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
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
      />
    </QueryClientProvider>,
  );
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

  expect(screen.getByText("2 sources still extracting")).toBeVisible();
  expect(screen.getByText("1 partially failed")).toBeVisible();
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
  renderLibrary({
    project,
    overview: buildOverview(buildNodes(1, "underwriting", "uw")),
    locale: "zh",
  });

  await userEvent.click(screen.getByRole("tab", { name: "知识图谱" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "搜索知识库" }),
    "uw0",
  );

  expect(screen.getByText(/概念/)).toBeVisible();
  expect(screen.queryByText(/CONCEPT/)).not.toBeInTheDocument();
});
