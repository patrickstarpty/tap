import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// Two parallel edges close enough that their labels overlap wherever they
// slide along their edges: only one of the two can be shown.
const PARALLEL_NODES: GraphNode[] = [
  buildNode({ id: "h", label: "Hub", degree: 1, x: 300, y: 300 }),
  buildNode({ id: "a", label: "Alpha", degree: 1, x: 500, y: 300 }),
  buildNode({ id: "b", label: "Beta", degree: 1, x: 300, y: 316 }),
  buildNode({ id: "c", label: "Gamma", degree: 1, x: 500, y: 316 }),
];
const PARALLEL_EDGES: GraphEdge[] = [
  {
    id: "ha",
    label: "requires",
    relationType: "REQUIRES",
    provenance: "extracted",
    source: "h",
    target: "a",
  },
  {
    id: "bc",
    label: "precedes",
    relationType: "PRECEDES",
    provenance: "extracted",
    source: "b",
    target: "c",
  },
];

function shownEdgeLabels(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".tap-graph-edge-label text")].map(
    (text) => text.textContent ?? "",
  );
}

it("hides an edge label that would overlap another, keeping the hovered node's edge", () => {
  const { container } = render(
    <KnowledgeGraph
      {...props({ nodes: PARALLEL_NODES, edges: PARALLEL_EDGES })}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Zoom in" }));

  expect(shownEdgeLabels(container)).toEqual(["requires"]);

  fireEvent.mouseEnter(nodeGroup("Gamma"));
  expect(shownEdgeLabels(container)).toEqual(["precedes"]);
});

it("keeps a cited edge's label over the hovered node's edge", () => {
  const { container } = render(
    <KnowledgeGraph
      {...props({
        nodes: PARALLEL_NODES,
        edges: PARALLEL_EDGES,
        highlight: { edgeIds: new Set(["bc"]), nodeIds: new Set(["b", "c"]) },
      })}
    />,
  );
  fireEvent.mouseEnter(nodeGroup("Alpha"));

  expect(shownEdgeLabels(container)).toEqual(["precedes"]);
});

// A canvas laid out at the SVG's own aspect ratio (one screen pixel per
// view-box unit) with the floating toolbar over its top-right corner.
function mockCanvasLayout() {
  const rect = (left: number, top: number, right: number, bottom: number) =>
    DOMRect.fromRect({
      x: left,
      y: top,
      width: right - left,
      height: bottom - top,
    });
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
    function (this: Element) {
      if (this.classList.contains("tap-graph-toolbar")) {
        return rect(1300, 12, 1596, 60);
      }
      if (
        this.classList.contains("tap-graph-canvas") ||
        this.tagName.toLowerCase() === "svg"
      ) {
        return rect(0, 0, 1608, 1120);
      }
      return rect(0, 0, 0, 0);
    },
  );
}

it("keeps labels out from under the floating toolbar", async () => {
  mockCanvasLayout();
  render(
    <KnowledgeGraph
      {...props({
        nodes: [
          buildNode({ id: "t", label: "Node", degree: 0, x: 1262, y: 20 }),
        ],
      })}
    />,
  );

  // Below would run under the toolbar; above leaves the canvas.
  await waitFor(() => expect(placementOf("Node")).toBe("left"));
});

it("re-places labels at the viewport edge once panning settles", async () => {
  mockCanvasLayout();
  render(
    <KnowledgeGraph
      {...props({
        nodes: [
          buildNode({
            id: "far",
            label: "Far right label",
            degree: 0,
            x: 1400,
            y: 600,
          }),
        ],
      })}
    />,
  );
  await waitFor(() => expect(placementOf("Far right label")).toBe("below"));

  // Drag the view right, bringing the node to the canvas's right edge.
  const svg = screen.getByRole("group", {
    name: WORKSPACE_COPY.en.library.knowledgeGraphImage,
  });
  fireEvent.pointerDown(svg, { button: 0, pointerId: 1, clientX: 500 });
  fireEvent.pointerMove(svg, { pointerId: 1, clientX: 670 });
  fireEvent.pointerUp(svg, { pointerId: 1, clientX: 670 });
  expect(svg.querySelector("g")?.getAttribute("transform")).toBe(
    "translate(170 0) scale(1)",
  );
  // Placement waits for the view to settle rather than following the drag.
  expect(placementOf("Far right label")).toBe("below");

  // Centered below, the label would run off the right edge.
  await waitFor(() => expect(placementOf("Far right label")).toBe("left"));
});
