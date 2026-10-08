import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type { GraphNodeDetail } from "../model/graph";
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
    nodeDetailsLoading: "Loading node details…",
    nodeDetailsError: "Node details are unavailable. Try again.",
    nodeTypes: {
      ENTITY: "Entity",
      CONCEPT: "Concept",
      REQUIREMENT: "Requirement",
      SYSTEM: "System",
      ACTOR: "Actor",
      PROCESS: "Process",
    },
  },
} as unknown as WorkspaceCopy;

function queryResult(data: GraphNodeDetail | undefined) {
  return {
    data,
    isPending: data === undefined,
    isError: false,
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

function renderPanel(
  detail: GraphNodeDetail,
  props: Partial<Parameters<typeof NodeDetailPanel>[0]> = {},
) {
  vi.mocked(useGraphNode).mockReturnValue(queryResult(detail));
  const copy = TEST_COPY;
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
        copy={copy}
        locale="en"
        onClose={vi.fn()}
        onSelectNode={vi.fn()}
        {...props}
      />
    </QueryClientProvider>,
  );
}

it("shows type, aliases, sources and grouped relations", () => {
  renderPanel(buildDetail());

  expect(screen.getByText("Health disclosure")).toBeVisible();
  expect(screen.getByText("Concept")).toBeVisible();
  expect(screen.getByText("HD")).toBeVisible();
  expect(screen.getAllByText("policy-a.md").length).toBeGreaterThan(0);
  expect(screen.getByText(/Requires \(REQUIRES\)/)).toBeVisible();
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

  expect(screen.getByText(/Requires \(REQUIRES\)/)).toBeVisible();
  expect(screen.getByText(/Applies to \(APPLIES_TO\)/)).toBeVisible();
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
