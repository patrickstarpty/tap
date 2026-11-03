import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import { createKnowledgeClient } from "../../../features/knowledge/api/client";
import {
  useActiveGraph,
  useGraphNode,
  useGraphOverview,
  useGraphSearch,
} from "../../../features/graph/api/queries";
import { WORKSPACE_COPY } from "./copy";
import { LibraryWorkspace } from "./LibraryWorkspace";
import type { LibrarySource } from "./model";

vi.mock("../../../features/knowledge/api/client", () => ({
  createKnowledgeClient: vi.fn(),
}));
vi.mock("../../../features/graph/api/queries", () => ({
  useActiveGraph: vi.fn(),
  useGraphSearch: vi.fn(),
  useGraphOverview: vi.fn(),
  useGraphNode: vi.fn(),
}));

const SOURCES: LibrarySource[] = [
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
];

const OVERVIEW = {
  nodes: [
    {
      overviewNodeId: "overview:login",
      label: "Login service",
      nodeType: "ENTITY",
      degree: 1,
      members: [
        { snapshotId: "snap_rev_src_a", nodeId: "a-login", sourceId: "src_a" },
        { snapshotId: "snap_rev_src_b", nodeId: "b-login", sourceId: "src_b" },
      ],
    },
    {
      overviewNodeId: "overview:auth",
      label: "Authentication",
      nodeType: "SYSTEM",
      degree: 1,
      members: [
        { snapshotId: "snap_rev_src_a", nodeId: "a-auth", sourceId: "src_a" },
      ],
    },
  ],
  edges: [
    {
      overviewEdgeId: "overview-edge:1",
      source: "overview:login",
      target: "overview:auth",
      relationType: "DEPENDS_ON",
      support: 2,
    },
  ],
  stats: {
    sourceCount: 2,
    nodeTypeCounts: { ENTITY: 1, SYSTEM: 1 },
    truncated: false,
  },
  snapshotIds: ["snap_rev_src_a", "snap_rev_src_b"],
};

function mockOverview(data: unknown) {
  vi.mocked(useGraphOverview).mockReturnValue({
    data,
    isPending: false,
    isError: false,
  } as never);
}

beforeEach(() => {
  vi.mocked(createKnowledgeClient).mockReturnValue({
    getSource: vi.fn(async (sourceId: string) => ({
      documents: {
        items: [{ status: "ready", revisionId: `rev_${sourceId}` }],
      },
    })),
  } as never);
  vi.mocked(useActiveGraph).mockImplementation(
    (_projectId, revisionIds) =>
      ({
        data: revisionIds.length
          ? { items: [{ snapshotId: `snap_${revisionIds[0]}` }] }
          : undefined,
        isPending: revisionIds.length === 0,
        isError: false,
      }) as never,
  );
  vi.mocked(useGraphSearch).mockImplementation(
    (_projectId, snapshotId) =>
      ({
        data: snapshotId
          ? {
              snapshotId,
              nodes: [
                {
                  nodeId: snapshotId.endsWith("src_b") ? "b-login" : "a-login",
                  nodeType: "ENTITY",
                  label: "Login service",
                  canonicalKey: "login-service",
                },
              ],
              edges: [],
            }
          : undefined,
        isPending: snapshotId === null,
        isError: false,
      }) as never,
  );
  vi.mocked(useGraphNode).mockImplementation(
    (_projectId, snapshotId, nodeId) =>
      ({
        data:
          snapshotId && nodeId
            ? {
                snapshotId,
                nodes: [
                  {
                    nodeId,
                    nodeType: "ENTITY",
                    label:
                      nodeId === "a-auth" ? "Authentication" : "Login service",
                    canonicalKey: nodeId,
                  },
                ],
                edges: [],
              }
            : undefined,
        isPending: !(snapshotId && nodeId),
        isError: false,
      }) as never,
  );
  mockOverview(OVERVIEW);
});

function renderLibrary(
  options: { locale?: "en" | "zh"; projectId?: string | null } = {},
) {
  const locale = options.locale ?? "en";
  const projectId =
    options.projectId === undefined ? "tapper-demo" : options.projectId;
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <LibraryWorkspace
        copy={WORKSPACE_COPY[locale]}
        locale={locale}
        graphProjectId={projectId ?? undefined}
        sources={SOURCES}
      />
    </QueryClientProvider>,
  );
}

async function openGraph(name = "Knowledge Graph") {
  const tab = screen.queryByRole("tab", { name, selected: false });
  if (tab) await userEvent.click(tab);
}

it("shows the domain overview by default", async () => {
  renderLibrary();
  await openGraph();

  expect(vi.mocked(useGraphOverview).mock.lastCall?.[0]).toBe("tapper-demo");
  expect(screen.getByText("Domain overview")).toBeVisible();
  expect(screen.getByRole("button", { name: /Login service/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /Authentication/ })).toBeVisible();
  expect(
    screen.getByText("Domain overview · merged from 2 published sources."),
  ).toBeVisible();
  expect(
    screen.queryByRole("combobox", { name: "Graph source" }),
  ).not.toBeInTheDocument();
});

it("keeps the published source graph behind the view switch", async () => {
  renderLibrary();
  await openGraph();

  await userEvent.click(screen.getByText("Published source graph"));

  expect(screen.getByRole("combobox", { name: "Graph source" })).toBeVisible();
  await waitFor(() =>
    expect(
      screen.getByText(/nodes and relationships come from the service/i),
    ).toBeVisible(),
  );
  expect(vi.mocked(useActiveGraph).mock.lastCall?.[1]).toEqual(["rev_src_a"]);
});

it("explains an empty domain overview in English and Chinese", async () => {
  mockOverview({ ...OVERVIEW, nodes: [], edges: [], snapshotIds: [] });
  const { unmount } = renderLibrary();
  await openGraph();
  expect(
    screen.getByText("No published knowledge has produced a graph yet"),
  ).toBeVisible();
  unmount();

  renderLibrary({ locale: "zh" });
  await openGraph("知识图谱");
  expect(screen.getByText("尚无已发布知识生成的图谱")).toBeVisible();
});

it("keeps the existing no-project state", async () => {
  renderLibrary({ projectId: null });

  expect(screen.getByText("No project is selected.")).toBeVisible();
  expect(screen.queryByText("Domain overview")).not.toBeInTheDocument();
});

it("jumps from an overview member to its source graph node", async () => {
  renderLibrary();
  await openGraph();

  await userEvent.click(screen.getByRole("button", { name: /Login service/ }));
  const details = screen.getByRole("region", { name: "Node details" });
  const members = within(details).getByRole("list", {
    name: "Appears in sources",
  });
  await userEvent.click(
    within(members).getByRole("button", { name: "Claims control" }),
  );

  const select = screen.getByRole("combobox", {
    name: "Graph source",
  }) as HTMLSelectElement;
  expect(select.value).toBe("src_b");
  await waitFor(() =>
    expect(vi.mocked(useActiveGraph).mock.lastCall?.[1]).toEqual(["rev_src_b"]),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /Login service/, pressed: true }),
    ).toBeVisible(),
  );
});

it("loads and selects a member node outside the first source graph page", async () => {
  vi.mocked(useGraphSearch).mockImplementation(
    (_projectId, snapshotId) =>
      ({
        data: snapshotId
          ? {
              snapshotId,
              nodes: Array.from({ length: 50 }, (_, index) => ({
                nodeId: `filler-${index}`,
                nodeType: "ENTITY",
                label: `Filler ${index}`,
                canonicalKey: `filler-${index}`,
              })),
              edges: [],
            }
          : undefined,
        isPending: snapshotId === null,
        isError: false,
      }) as never,
  );
  renderLibrary();
  await openGraph();

  await userEvent.click(screen.getByRole("button", { name: /Authentication/ }));
  const details = screen.getByRole("region", { name: "Node details" });
  await userEvent.click(
    within(details).getByRole("button", { name: "Underwriting rules" }),
  );

  await waitFor(() =>
    expect(vi.mocked(useGraphNode).mock.lastCall?.slice(1)).toEqual([
      "snap_rev_src_a",
      "a-auth",
    ]),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: /Authentication/, pressed: true }),
    ).toBeVisible(),
  );
});

it("marks a truncated domain overview", async () => {
  mockOverview({ ...OVERVIEW, stats: { ...OVERVIEW.stats, truncated: true } });
  renderLibrary();
  await openGraph();
  expect(
    screen.getByText(/only the 2,000 most recently updated sources/i),
  ).toBeVisible();
});

it("omits the provenance row for an overview node without relationships", async () => {
  mockOverview({ ...OVERVIEW, nodes: [OVERVIEW.nodes[0]], edges: [] });
  renderLibrary();
  await openGraph();

  await userEvent.click(screen.getByRole("button", { name: /Login service/ }));
  const details = screen.getByRole("region", { name: "Node details" });
  expect(within(details).queryByText("Provenance")).not.toBeInTheDocument();
});
