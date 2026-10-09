import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { WORKSPACE_COPY } from "./copy";
import { KnowledgeGraph, type KnowledgeGraphProps } from "./KnowledgeGraph";
import type { GraphEdge, GraphNode } from "./graphLayout";

function buildNode(overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id: "n1",
    label: "n1",
    nodeType: "CONCEPT",
    community: "c1",
    communityLabel: "Community",
    color: "#2563eb",
    degree: 1,
    aliases: [],
    x: 0,
    y: 0,
    ...overrides,
  };
}

const EDGE: GraphEdge = {
  id: "e1",
  label: "requires",
  relationType: "REQUIRES",
  provenance: "extracted",
  source: "n1",
  target: "n2",
};

function baseProps(
  overrides: Partial<KnowledgeGraphProps> = {},
): KnowledgeGraphProps {
  return {
    copy: WORKSPACE_COPY.en,
    nodes: [],
    edges: [],
    communities: [
      { communityId: "c1", label: "Community", color: "#2563eb", size: 2 },
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

// A tight bounding box (both nodes at the same point) fits at the max zoom;
// a bounding box spanning the whole canvas fits at a much lower zoom — used
// below to tell whether a given render used the "near" or "far" positions.
const NEAR_POSITIONS: GraphNode[] = [
  buildNode({ id: "n1", label: "n1", x: 780, y: 560 }),
  buildNode({ id: "n2", label: "n2", x: 780, y: 560 }),
];
const FAR_POSITIONS: GraphNode[] = [
  buildNode({ id: "n1", label: "n1", x: 0, y: 0 }),
  buildNode({ id: "n2", label: "n2", x: 1560, y: 1120 }),
];

const HIGHLIGHT = {
  edgeIds: new Set(["e1"]),
  nodeIds: new Set(["n1", "n2"]),
};

it("re-fits the viewport once the highlighted nodes' positions change (e.g. the layout worker replacing provisional positions)", () => {
  const { rerender } = render(
    <KnowledgeGraph
      {...baseProps({
        nodes: NEAR_POSITIONS,
        edges: [EDGE],
        highlight: HIGHLIGHT,
      })}
    />,
  );
  const nearZoom = screen.getByRole("status", {
    name: "Zoom level",
  }).textContent;

  rerender(
    <KnowledgeGraph
      {...baseProps({
        nodes: FAR_POSITIONS,
        edges: [EDGE],
        highlight: HIGHLIGHT,
      })}
    />,
  );

  const farZoom = screen.getByRole("status", {
    name: "Zoom level",
  }).textContent;
  expect(farZoom).not.toBe(nearZoom);
});

it("skips the re-fit once the user has panned since the highlight was applied", () => {
  const { rerender } = render(
    <KnowledgeGraph
      {...baseProps({
        nodes: NEAR_POSITIONS,
        edges: [EDGE],
        highlight: HIGHLIGHT,
      })}
    />,
  );
  const zoomBeforePan = screen.getByRole("status", {
    name: "Zoom level",
  }).textContent;

  const svg = document.querySelector(".tap-graph-canvas svg")!;
  svg.dispatchEvent(
    new PointerEvent("pointerdown", {
      button: 0,
      pointerId: 1,
      clientX: 0,
      clientY: 0,
      bubbles: true,
    }),
  );

  rerender(
    <KnowledgeGraph
      {...baseProps({
        nodes: FAR_POSITIONS,
        edges: [EDGE],
        highlight: HIGHLIGHT,
      })}
    />,
  );

  const zoomAfterPositionsChange = screen.getByRole("status", {
    name: "Zoom level",
  }).textContent;
  expect(zoomAfterPositionsChange).toBe(zoomBeforePan);
});
