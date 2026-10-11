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

function labelGroupOf(name: string): SVGGElement | null {
  return nodeGroup(name).querySelector(".tap-graph-node-label-group");
}

function placementOf(name: string): string | null | undefined {
  return labelGroupOf(name)?.getAttribute("data-label-placement");
}

it("labels every node, moving crowded labels above or beside their nodes", () => {
  render(<KnowledgeGraph {...props()} />);

  for (const node of CROWDED_ROW) {
    expect(labelGroupOf(node.label)?.textContent).toBe(node.label);
    expect(labelGroupOf(node.label)?.getAttribute("data-halo")).toBe("false");
  }
  expect(placementOf("Policy renewal")).toBe("below");
  expect(placementOf("Beneficiary update")).toBe("above");
  expect(placementOf("Address change")).toBe("left");
  expect(
    labelGroupOf("Address change")
      ?.querySelector("text")
      ?.getAttribute("text-anchor"),
  ).toBe("end");
});

it("never moves node circles to make room for labels", () => {
  render(<KnowledgeGraph {...props()} />);

  for (const node of CROWDED_ROW) {
    const core = nodeGroup(node.label).querySelector(".tap-graph-node-core");
    expect(core?.getAttribute("cx")).toBe(String(node.x));
    expect(core?.getAttribute("cy")).toBe(String(node.y));
  }
});

it("places the hovered or focused node's label first", () => {
  render(<KnowledgeGraph {...props()} />);

  // Below would cover the neighboring "Policy renewal" node, so the
  // hovered label takes the space above, and "Beneficiary update" yields it.
  fireEvent.mouseEnter(nodeGroup("Address change"));
  expect(placementOf("Address change")).toBe("above");
  expect(placementOf("Beneficiary update")).not.toBe("above");

  fireEvent.mouseLeave(nodeGroup("Address change"));
  expect(placementOf("Address change")).toBe("left");

  fireEvent.focus(nodeGroup("Address change"));
  expect(placementOf("Address change")).toBe("above");
});

it("places the selected node's label first", () => {
  render(<KnowledgeGraph {...props({ selectedNodeId: "address" })} />);

  expect(placementOf("Address change")).toBe("above");
  expect(placementOf("Beneficiary update")).not.toBe("above");
});

it("re-places labels in screen space when zooming; labels grow slower than distances", () => {
  const pair = [
    buildNode({ id: "a", label: "Left label", degree: 2, x: 400, y: 400 }),
    buildNode({ id: "b", label: "Right label", x: 500, y: 400 }),
  ];
  render(<KnowledgeGraph {...props({ nodes: pair })} />);
  expect(placementOf("Right label")).not.toBe("below");

  const zoomIn = screen.getByRole("button", { name: "Zoom in" });
  fireEvent.click(zoomIn);
  fireEvent.click(zoomIn);
  fireEvent.click(zoomIn);

  expect(screen.getByRole("status", { name: "Zoom level" }).textContent).toBe(
    "175%",
  );
  expect(labelGroupOf("Right label")?.getAttribute("transform")).toContain(
    `scale(${String(1 / Math.sqrt(1.75))})`,
  );
  expect(placementOf("Left label")).toBe("below");
  expect(placementOf("Right label")).toBe("below");
});
