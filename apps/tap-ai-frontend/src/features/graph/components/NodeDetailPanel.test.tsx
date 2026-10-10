import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type { GraphNodeDetail } from "../model/graph";
import { GraphVersionConflictError } from "../api/client";
import { useGraphNode } from "../api/queries";
// `import type` only — a features/ module may not value-import from
// widgets/ (see the identical note in `CommunityList.tsx`), so this test
// builds its own minimal copy fixture below rather than importing the
// real `WORKSPACE_COPY`.
import type { WorkspaceCopy } from "../../../widgets/tap/workspace/copy";
import { NodeDetailPanel, RELATION_GROUP_PREVIEW } from "./NodeDetailPanel";

vi.mock("../api/queries", () => ({
  useGraphNode: vi.fn(),
}));

const TEST_COPY = {
  sources: {
    retry: "Retry loading",
  },
  library: {
    nodeDetails: "Node details",
    closeNodeDetails: "Close node details",
    community: "Topic group",
    relationships: "Relationships",
    connections: "connections",
    aliases: "Aliases",
    nodeSources: "Sources",
    evidenceSnippets: "Evidence",
    openOriginal: "Open original",
    askAboutNode: "Ask about this",
    showAllRelations: (n: number) => `Show all (${n})`,
    relationCount: (n: number) => `${n} relations`,
    relationIncoming: "Incoming",
    relationOutgoing: "Outgoing",
    nodeDetailsLoading: "Loading node details…",
    nodeDetailsError: "Node details are unavailable. Try again.",
    otherCommunity: "Other",
    nodeTypes: {
      ENTITY: "Entity",
      CONCEPT: "Concept",
      REQUIREMENT: "Requirement",
      SYSTEM: "System",
      ACTOR: "Actor",
      PROCESS: "Process",
    },
    relationTypes: {
      REQUIRES: "Requires",
      APPLIES_TO: "Applies to",
      PART_OF: "Part of",
      EXCEPTION_OF: "Exception of",
      SUPERSEDES: "Supersedes",
      TRIGGERS: "Triggers",
      PRECEDES: "Precedes",
      VALIDATED_BY: "Validated by",
      RESPONSIBLE_FOR: "Responsible for",
      USES: "Uses",
      DEFINES: "Defines",
      CONFLICTS_WITH: "Conflicts with",
      RELATED_TO: "Related to",
    },
  },
} as unknown as WorkspaceCopy;

function queryResult(
  data: GraphNodeDetail | undefined,
  overrides: Partial<{
    isPending: boolean;
    isError: boolean;
    error: unknown;
    refetch: () => void;
  }> = {},
) {
  return {
    data,
    isPending:
      overrides.isPending ??
      (overrides.isError === true ? false : data === undefined),
    isError: overrides.isError ?? false,
    error: overrides.error,
    refetch: overrides.refetch ?? vi.fn(),
  } as never;
}

function buildEvidence(
  overrides: Partial<
    GraphNodeDetail["sources"] extends (infer S)[] | undefined ? S : never
  > = {},
) {
  return {
    sourceRevisionId: "src_a",
    documentRevisionId: "doc_a",
    sourceName: "policy-a.md",
    evidence: [
      {
        chunkId: "c1",
        ownerId: "n1",
        ownerKind: "node" as const,
        documentRevisionId: "doc_a",
        sourceRevisionId: "src_a",
        snippet: "Applicants must disclose pre-existing conditions.",
        anchor: {},
        contentDigest: null,
      },
    ],
    ...overrides,
  };
}

function buildDetail(
  overrides: Partial<GraphNodeDetail> = {},
): GraphNodeDetail {
  return {
    graphVersion: 1,
    node: {
      nodeId: "n1",
      label: "Health disclosure",
      nodeType: "CONCEPT",
      canonicalKey: "health-disclosure",
      degree: 2,
      communityId: "underwriting",
      aliases: ["HD"],
    },
    community: {
      communityId: "underwriting",
      label: "Underwriting",
      size: 5,
    },
    sources: [
      buildEvidence(),
      buildEvidence({
        sourceRevisionId: "src_b",
        documentRevisionId: "doc_b",
        sourceName: "policy-b.md",
        evidence: [
          {
            chunkId: "c2",
            ownerId: "n1",
            ownerKind: "node" as const,
            documentRevisionId: "doc_b",
            sourceRevisionId: "src_b",
            snippet: "Health conditions affecting eligibility.",
            anchor: {},
            contentDigest: null,
          },
        ],
      }),
    ],
    relations: [
      {
        relationType: "REQUIRES",
        edges: [
          {
            edgeId: "e1",
            sourceNodeId: "n1",
            targetNodeId: "n2",
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
        nodeId: "n2",
        label: "Medical exam",
        nodeType: "CONCEPT",
        canonicalKey: "n2",
        degree: 1,
        aliases: [],
      },
    ],
    ...overrides,
  };
}

function renderPanelWithMock(
  mockReturn: ReturnType<typeof queryResult>,
  props: Partial<Parameters<typeof NodeDetailPanel>[0]> = {},
) {
  vi.mocked(useGraphNode).mockReturnValue(mockReturn);
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <NodeDetailPanel
        projectId="proj_1"
        graphVersion={1}
        nodeId="n1"
        color="#123456"
        communityLabel="Underwriting"
        copy={TEST_COPY}
        locale="en"
        onClose={vi.fn()}
        onSelectNode={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
}

function renderPanel(
  detail: GraphNodeDetail,
  props: Partial<Parameters<typeof NodeDetailPanel>[0]> = {},
) {
  return renderPanelWithMock(queryResult(detail), props);
}

it("shows type, aliases, sources and grouped relations", () => {
  renderPanel(buildDetail());

  expect(screen.getByText("Health disclosure")).toBeVisible();
  expect(screen.getByText("Concept")).toBeVisible();
  expect(screen.getByText("HD")).toBeVisible();
  expect(screen.getAllByText("policy-a.md").length).toBeGreaterThan(0);
  expect(screen.getByText(/Requires \(Requires\)/)).toBeVisible();
  expect(screen.getByRole("button", { name: /Medical exam/ })).toBeVisible();
});

it("groups forty relations by type and limits each group", async () => {
  const user = userEvent.setup();
  const requiresEdges = Array.from({ length: 20 }, (_, index) => ({
    edgeId: `req-${index}`,
    sourceNodeId: "n1",
    targetNodeId: `req-target-${index}`,
    relationType: "REQUIRES",
    relationLabel: "Requires",
    confidence: 0.9,
    origin: "EXTRACTED" as const,
  }));
  const appliesEdges = Array.from({ length: 20 }, (_, index) => ({
    edgeId: `app-${index}`,
    sourceNodeId: "n1",
    targetNodeId: `app-target-${index}`,
    relationType: "APPLIES_TO",
    relationLabel: "Applies to",
    confidence: 0.9,
    origin: "EXTRACTED" as const,
  }));
  const neighbors = [
    ...requiresEdges.map((edge, index) => ({
      nodeId: edge.targetNodeId,
      label: `Requires target ${index}`,
      nodeType: "CONCEPT",
      canonicalKey: edge.targetNodeId,
      degree: 1,
      aliases: [],
    })),
    ...appliesEdges.map((edge, index) => ({
      nodeId: edge.targetNodeId,
      label: `Applies target ${index}`,
      nodeType: "CONCEPT",
      canonicalKey: edge.targetNodeId,
      degree: 1,
      aliases: [],
    })),
  ];

  renderPanel(
    buildDetail({
      relations: [
        { relationType: "REQUIRES", edges: requiresEdges },
        { relationType: "APPLIES_TO", edges: appliesEdges },
      ],
      neighbors,
    }),
  );

  expect(screen.getByText(/Requires \(Requires\)/)).toBeVisible();
  expect(screen.getByText(/Applies to \(Applies to\)/)).toBeVisible();
  expect(screen.getAllByRole("listitem")).toHaveLength(
    RELATION_GROUP_PREVIEW * 2,
  );

  const showAllButtons = screen.getAllByRole("button", {
    name: "Show all (20)",
  });
  expect(showAllButtons).toHaveLength(2);
  await user.click(showAllButtons[0]);

  expect(screen.getAllByRole("listitem")).toHaveLength(
    RELATION_GROUP_PREVIEW + 20,
  );
});

it("asks about the node with its sources", async () => {
  const user = userEvent.setup();
  const onAskAboutNode = vi.fn();
  renderPanel(buildDetail(), { onAskAboutNode });

  await user.click(screen.getByRole("button", { name: "Ask about this" }));

  expect(onAskAboutNode).toHaveBeenCalledWith("Health disclosure", [
    "src_a",
    "src_b",
  ]);
});

it("opens the original source from an evidence snippet", async () => {
  const user = userEvent.setup();
  const onOpenSource = vi.fn();
  renderPanel(buildDetail(), { onOpenSource });

  const firstSnippet = screen.getByText(
    "Applicants must disclose pre-existing conditions.",
  );
  const snippetGroup =
    firstSnippet.closest("li") ?? firstSnippet.parentElement!;
  await user.click(
    within(snippetGroup as HTMLElement).getByRole("button", {
      name: "Open original",
    }),
  );

  expect(onOpenSource).toHaveBeenCalledWith("src_a", expect.anything());
});

it("shows the relation direction for incoming and outgoing edges", () => {
  renderPanel(
    buildDetail({
      relations: [
        {
          relationType: "REQUIRES",
          edges: [
            {
              edgeId: "e-out",
              sourceNodeId: "n1",
              targetNodeId: "n2",
              relationType: "REQUIRES",
              relationLabel: "Requires",
              confidence: 0.9,
              origin: "EXTRACTED",
            },
            {
              edgeId: "e-in",
              sourceNodeId: "n3",
              targetNodeId: "n1",
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
          nodeId: "n2",
          label: "Medical exam",
          nodeType: "CONCEPT",
          canonicalKey: "n2",
          degree: 1,
          aliases: [],
        },
        {
          nodeId: "n3",
          label: "Underwriting policy",
          nodeType: "CONCEPT",
          canonicalKey: "n3",
          degree: 1,
          aliases: [],
        },
      ],
    }),
  );

  const outgoingItem = screen
    .getByRole("button", { name: /Medical exam/ })
    .closest("li")!;
  const incomingItem = screen
    .getByRole("button", { name: /Underwriting policy/ })
    .closest("li")!;
  expect(within(outgoingItem).getByText("Outgoing")).toBeVisible();
  expect(within(incomingItem).getByText("Incoming")).toBeVisible();
});

it("never shows the raw relation type code, even for an unmapped locale string", () => {
  const zhCopy = {
    ...TEST_COPY,
    library: {
      ...TEST_COPY.library,
      relationTypes: {
        ...TEST_COPY.library.relationTypes,
        REQUIRES: "需要",
      },
    },
  } as unknown as typeof TEST_COPY;
  renderPanel(buildDetail(), { copy: zhCopy, locale: "zh" });

  expect(screen.getByText(/Requires \(需要\)/)).toBeVisible();
  expect(screen.queryByText(/REQUIRES/)).not.toBeInTheDocument();
});

it("falls back to the node detail's own community and a neutral color when the overview has no color/communityLabel for this node", () => {
  renderPanel(buildDetail(), { color: undefined, communityLabel: undefined });

  // `buildDetail()`'s `community.label` is "Underwriting" — confirms the
  // fallback reads `detail.community`, not just a hardcoded default.
  expect(screen.getByText("Underwriting")).toBeVisible();
});

it("moves focus to the first newly-shown relation item after Show all", async () => {
  const user = userEvent.setup();
  const edges = Array.from({ length: 15 }, (_, index) => ({
    edgeId: `e-${index}`,
    sourceNodeId: "n1",
    targetNodeId: `t-${index}`,
    relationType: "REQUIRES",
    relationLabel: "Requires",
    confidence: 0.9,
    origin: "EXTRACTED" as const,
  }));
  const neighbors = edges.map((edge, index) => ({
    nodeId: edge.targetNodeId,
    label: `Target ${index}`,
    nodeType: "CONCEPT",
    canonicalKey: edge.targetNodeId,
    degree: 1,
    aliases: [],
  }));
  renderPanel(
    buildDetail({
      relations: [{ relationType: "REQUIRES", edges }],
      neighbors,
    }),
  );

  await user.click(screen.getByRole("button", { name: "Show all (15)" }));

  expect(screen.getByRole("button", { name: /Target 10/ })).toHaveFocus();
});

it("hides Open original when the source can't be resolved to a real source id", () => {
  renderPanel(buildDetail(), {
    onOpenSource: vi.fn(),
    canOpenSource: () => false,
  });

  expect(
    screen.queryByRole("button", { name: "Open original" }),
  ).not.toBeInTheDocument();
});

it("shows the header, close button and a working retry button on a genuine fetch error", async () => {
  const user = userEvent.setup();
  const refetch = vi.fn();
  const onClose = vi.fn();
  renderPanelWithMock(
    queryResult(undefined, {
      isError: true,
      error: new Error("boom"),
      refetch,
    }),
    { onClose },
  );

  expect(screen.getByRole("heading", { name: "Node details" })).toBeVisible();
  const closeButton = screen.getByRole("button", {
    name: "Close node details",
  });
  expect(closeButton).toBeVisible();
  expect(
    screen.getByText("Node details are unavailable. Try again."),
  ).toBeVisible();

  await user.click(screen.getByRole("button", { name: "Retry loading" }));
  expect(refetch).toHaveBeenCalled();

  await user.click(closeButton);
  expect(onClose).toHaveBeenCalled();
});

it("treats a stale-version 409 as still loading, with no error alert", () => {
  renderPanelWithMock(
    queryResult(undefined, {
      isError: true,
      error: new GraphVersionConflictError(),
    }),
  );

  expect(screen.getByText("Loading node details…")).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  // The header stays mounted even in this pseudo-loading state.
  expect(screen.getByRole("heading", { name: "Node details" })).toBeVisible();
});

it("shows the error body with a working retry button once the project refetch has settled and the version conflict persists", async () => {
  const user = userEvent.setup();
  const refetch = vi.fn();
  renderPanelWithMock(
    queryResult(undefined, {
      isError: true,
      error: new GraphVersionConflictError(),
      refetch,
    }),
    { projectRefetchSettled: true },
  );

  expect(screen.queryByText("Loading node details…")).not.toBeInTheDocument();
  expect(
    screen.getByText("Node details are unavailable. Try again."),
  ).toBeVisible();

  await user.click(screen.getByRole("button", { name: "Retry loading" }));
  expect(refetch).toHaveBeenCalled();
});
