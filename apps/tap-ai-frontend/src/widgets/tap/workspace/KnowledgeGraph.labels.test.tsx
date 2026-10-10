import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { WORKSPACE_COPY } from "./copy";
import { KnowledgeGraph, type KnowledgeGraphProps } from "./KnowledgeGraph";
import type { GraphNode } from "./graphLayout";

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

// Three nodes on nearly the same row, close enough that their labels cannot
// all fit below or above their nodes (the crowded "Address change / Policy
// renewal / Beneficiary update" cluster of the overview fixture).
const CROWDED_ROW: GraphNode[] = [
  buildNode({ id: "address", label: "Address change", x: 400, y: 400 }),
  buildNode({
    id: "renewal",
    label: "Policy renewal",
    degree: 4,
    x: 440,
    y: 405,
  }),
  buildNode({
    id: "beneficiary",
    label: "Beneficiary update",
    degree: 2,
    x: 480,
    y: 395,
  }),
];

function props(
  overrides: Partial<KnowledgeGraphProps> = {},
): KnowledgeGraphProps {
  return {
    copy: WORKSPACE_COPY.en,
    nodes: CROWDED_ROW,
    edges: [],
    communities: [
      { communityId: "c1", label: "Servicing", color: "#0f766e", size: 3 },
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

function nodeGroup(name: string): SVGGElement {
  return screen.getByRole("button", {
    name: new RegExp(`^${name} ·`, "u"),
  }) as unknown as SVGGElement;
}

function labelOf(name: string): SVGTextElement | null {
  return nodeGroup(name).querySelector(".tap-graph-node-label");
}

it("hides a crowded label and moves another above its node instead of overlapping", () => {
  render(<KnowledgeGraph {...props()} />);

  expect(labelOf("Policy renewal")?.textContent).toBe("Policy renewal");
  const above = labelOf("Beneficiary update");
  expect(above?.textContent).toBe("Beneficiary update");
  expect(Number(above?.getAttribute("y"))).toBeLessThan(395);
  expect(labelOf("Address change")).toBeNull();
});

it("never moves node circles to make room for labels", () => {
  render(<KnowledgeGraph {...props()} />);

  for (const node of CROWDED_ROW) {
    const core = nodeGroup(node.label).querySelector(".tap-graph-node-core");
    expect(core?.getAttribute("cx")).toBe(String(node.x));
    expect(core?.getAttribute("cy")).toBe(String(node.y));
  }
});

it("shows a hidden label while its node is hovered or focused", () => {
  render(<KnowledgeGraph {...props()} />);

  fireEvent.mouseEnter(nodeGroup("Address change"));
  expect(labelOf("Address change")?.textContent).toBe("Address change");

  fireEvent.mouseLeave(nodeGroup("Address change"));
  expect(labelOf("Address change")).toBeNull();

  fireEvent.focus(nodeGroup("Address change"));
  expect(labelOf("Address change")?.textContent).toBe("Address change");
});

it("always shows the selected node's label", () => {
  render(<KnowledgeGraph {...props({ selectedNodeId: "address" })} />);

  expect(labelOf("Address change")?.textContent).toBe("Address change");
});
