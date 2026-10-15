import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { WORKSPACE_COPY } from "./copy";
import { KnowledgeGraph, type KnowledgeGraphProps } from "./KnowledgeGraph";
import type { GraphEdge, GraphNode } from "./graphLayout";

// Regression coverage for the v9 overview evidence (real-model revalidation
// report 2, §9 Important 1): 31 node pairs in the published graph carry more
// than one relation (e.g. both EXCEPTION_OF and TRIGGERS between the same
// two nodes). The canvas itself never deduplicated edges -- the crash lived
// in the force-atlas worker's graphology instance (see
// `features/graph/workers/forceAtlasLayout.ts`) -- but this guards the
// render path end to end: given an overview with parallel and reversed
// edges between the same pair, `KnowledgeGraph` must render every edge
// without throwing.
function buildNode(overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id: "n1",
    label: "n1",
    nodeType: "CONCEPT",
    community: "c1",
    communityLabel: "Servicing",
    color: "#0f766e",
    degree: 1,
    aliases: [],
    x: 0,
    y: 0,
    ...overrides,
  };
}

function buildEdge(overrides: Partial<GraphEdge> = {}): GraphEdge {
  return {
    id: "e1",
    label: "related to",
    relationType: "RELATED_TO",
    provenance: "extracted",
    source: "a",
    target: "b",
    ...overrides,
  };
}

const NODES: GraphNode[] = [
  buildNode({ id: "a", label: "Coronary intervention", x: 400, y: 400 }),
  buildNode({ id: "b", label: "Insurance liability", x: 480, y: 420 }),
];

// Same pair of nodes, one edge each direction, plus a second relation in the
// forward direction -- three edges total between the same two nodes.
const PARALLEL_AND_REVERSED_EDGES: GraphEdge[] = [
  buildEdge({
    id: "e-exception",
    label: "exception of",
    relationType: "EXCEPTION_OF",
    source: "a",
    target: "b",
  }),
  buildEdge({
    id: "e-triggers",
    label: "triggers",
    relationType: "TRIGGERS",
    source: "a",
    target: "b",
  }),
  buildEdge({
    id: "e-reverse",
    label: "related to",
    relationType: "RELATED_TO",
    source: "b",
    target: "a",
  }),
];

function props(
  overrides: Partial<KnowledgeGraphProps> = {},
): KnowledgeGraphProps {
  return {
    copy: WORKSPACE_COPY.en,
    nodes: NODES,
    edges: PARALLEL_AND_REVERSED_EDGES,
    communities: [
      { communityId: "c1", label: "Servicing", color: "#0f766e", size: 2 },
    ],
    activeCommunities: new Set(["c1"]),
    onToggleCommunity: vi.fn(),
    onSelectAllCommunities: vi.fn(),
    communitiesFooter: { extracting: 0, partial: 0 },
    searchQuery: "",
    selectedNodeId: null,
    onSelectNode: vi.fn(),
    detailPanel: null,
    caption: "caption",
    ...overrides,
  };
}

it("renders every edge between a pair of nodes that has parallel and reversed relations, without throwing", () => {
  expect(() => render(<KnowledgeGraph {...props()} />)).not.toThrow();

  const edgeElements = document.querySelectorAll(".tap-graph-edge");
  expect(edgeElements).toHaveLength(PARALLEL_AND_REVERSED_EDGES.length);

  const nodeA = screen.getByRole("button", {
    name: /^Coronary intervention ·/u,
  });
  const nodeB = screen.getByRole("button", {
    name: /^Insurance liability ·/u,
  });
  expect(nodeA).toBeInTheDocument();
  expect(nodeB).toBeInTheDocument();
});
