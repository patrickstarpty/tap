import { describe, expect, it } from "vitest";

import {
  edgeLabelBox,
  estimateTextWidth,
  labelBox,
  placeGraphLabels,
  placeNodeLabels,
  type LabelBox,
  type LabelCandidate,
  type LabelPlacementOptions,
  type PlacedLabel,
} from "./labelPlacement";

const FONT_SIZE = 18;
const NO_IDS: ReadonlySet<string> = new Set();

function candidate(overrides: Partial<LabelCandidate> = {}): LabelCandidate {
  return {
    id: "n1",
    x: 0,
    y: 0,
    radius: 12,
    text: "Node",
    degree: 1,
    ...overrides,
  };
}

function options(
  overrides: Partial<LabelPlacementOptions> = {},
): LabelPlacementOptions {
  return {
    priority: NO_IDS,
    emphasized: NO_IDS,
    fontSize: FONT_SIZE,
    zoom: 1,
    ...overrides,
  };
}

function overlapArea(a: LabelBox, b: LabelBox): number {
  const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
  const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  return width > 0 && height > 0 ? width * height : 0;
}

function circleHitsBox(
  circle: { x: number; y: number; r: number },
  box: LabelBox,
): boolean {
  const nearestX = Math.min(Math.max(circle.x, box.left), box.right);
  const nearestY = Math.min(Math.max(circle.y, box.top), box.bottom);
  return (
    (nearestX - circle.x) ** 2 + (nearestY - circle.y) ** 2 < circle.r ** 2
  );
}

function boxesOf(
  nodes: LabelCandidate[],
  placed: Map<string, PlacedLabel>,
  zoom = 1,
): Map<string, LabelBox> {
  return new Map(
    nodes.map((node) => [
      node.id,
      labelBox(node, placed.get(node.id)!.placement, {
        fontSize: FONT_SIZE,
        zoom,
      }),
    ]),
  );
}

function expectNoOverlaps(boxes: LabelBox[]): void {
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      expect(overlapArea(boxes[i]!, boxes[j]!)).toBe(0);
    }
  }
}

describe("labelBox", () => {
  it("puts each placement on its side of the node", () => {
    const node = candidate({ x: 100, y: 100, radius: 20 });
    const at = (placement: "below" | "above" | "right" | "left") =>
      labelBox(node, placement, { fontSize: FONT_SIZE, zoom: 1 });
    expect(at("below").top).toBeGreaterThan(100);
    expect(at("above").bottom).toBeLessThan(100 - 20);
    expect(at("right").left).toBeGreaterThan(100 + 20);
    expect(at("left").right).toBeLessThan(100 - 20);
    expect((at("below").left + at("below").right) / 2).toBeCloseTo(100);
  });

  it("works in screen space: zoom spreads nodes apart but keeps label size", () => {
    const node = candidate({ x: 100, y: 100, text: "Some label" });
    const atOne = labelBox(node, "below", { fontSize: FONT_SIZE, zoom: 1 });
    const atTwo = labelBox(node, "below", { fontSize: FONT_SIZE, zoom: 2 });
    expect(atTwo.right - atTwo.left).toBeCloseTo(atOne.right - atOne.left);
    expect((atTwo.left + atTwo.right) / 2).toBeCloseTo(200);
  });
});

describe("edgeLabelBox", () => {
  it("is centered on the edge midpoint in screen space with a fixed size", () => {
    const box = edgeLabelBox(50, 40, "requires", 2);
    expect((box.left + box.right) / 2).toBeCloseTo(100);
    expect((box.top + box.bottom) / 2).toBeCloseTo(80);
    expect(box.bottom - box.top).toBe(24);
  });
});

describe("estimateTextWidth", () => {
  it("counts CJK characters as full-width and Latin ones as narrower", () => {
    expect(estimateTextWidth("健康告知", 18)).toBeGreaterThan(
      estimateTextWidth("abcd", 18),
    );
    expect(estimateTextWidth("健康告知", 18)).toBeGreaterThanOrEqual(4 * 18);
  });
});

describe("placeNodeLabels", () => {
  it("labels every node below itself when nothing collides", () => {
    const nodes = [
      candidate({ id: "a", x: 0, y: 0 }),
      candidate({ id: "b", x: 600, y: 0 }),
      candidate({ id: "c", x: 0, y: 400 }),
    ];
    const placed = placeNodeLabels(nodes, options());
    for (const node of nodes) {
      expect(placed.get(node.id)).toMatchObject({
        placement: "below",
        halo: false,
      });
    }
  });

  it("moves a colliding label above its node instead of overlapping a neighbor", () => {
    const nodes = [
      candidate({
        id: "lapse",
        x: 100,
        y: 100,
        text: "Policy lapse",
        degree: 3,
      }),
      candidate({ id: "reinstatement", x: 150, y: 100, text: "Reinstatement" }),
    ];
    const placed = placeNodeLabels(nodes, options());
    expect(placed.get("lapse")?.placement).toBe("below");
    expect(placed.get("reinstatement")?.placement).toBe("above");
    expectNoOverlaps([...boxesOf(nodes, placed).values()]);
  });

  it("uses the free space beside a node when above and below are taken, never hiding a label", () => {
    const nodes = [
      candidate({ id: "address", x: 100, y: 200, text: "Address change" }),
      candidate({
        id: "renewal",
        x: 140,
        y: 205,
        text: "Policy renewal",
        degree: 4,
      }),
      candidate({
        id: "beneficiary",
        x: 180,
        y: 195,
        text: "Beneficiary update",
        degree: 2,
      }),
    ];
    const placed = placeNodeLabels(nodes, options());
    expect(placed.size).toBe(3);
    expect(placed.get("renewal")?.placement).toBe("below");
    expect(placed.get("beneficiary")?.placement).toBe("above");
    expect(placed.get("address")?.placement).toBe("left");
    expect(placed.get("address")?.halo).toBe(false);
    expectNoOverlaps([...boxesOf(nodes, placed).values()]);
  });

  it("keeps labels off other nodes' circles", () => {
    // A node sits right where the hub's label would go below it.
    const hub = candidate({ id: "hub", x: 100, y: 100, degree: 5 });
    const under = candidate({ id: "under", x: 100, y: 135, radius: 8 });
    const placed = placeNodeLabels([hub, under], options());
    const hubBox = labelBox(hub, placed.get("hub")!.placement, {
      fontSize: FONT_SIZE,
      zoom: 1,
    });
    expect(placed.get("hub")?.placement).not.toBe("below");
    expect(circleHitsBox({ x: 100, y: 135, r: 8 }, hubBox)).toBe(false);
  });

  it("avoids visible edge labels", () => {
    const node = candidate({ id: "a", x: 100, y: 100 });
    const below = labelBox(node, "below", { fontSize: FONT_SIZE, zoom: 1 });
    const placed = placeNodeLabels([node], options({ obstacles: [below] }));
    expect(placed.get("a")).toMatchObject({ placement: "above", halo: false });
  });

  it("still labels a boxed-in node, at its least-overlapping spot with a halo", () => {
    const node = candidate({ id: "a", x: 100, y: 100, text: "Boxed in" });
    const metrics = { fontSize: FONT_SIZE, zoom: 1 };
    const sides = ["below", "above", "right", "left"] as const;
    // Every side is covered; the right one only partly.
    const rightBox = labelBox(node, "right", metrics);
    const obstacles = [
      labelBox(node, "below", metrics),
      labelBox(node, "above", metrics),
      labelBox(node, "left", metrics),
      { ...rightBox, left: rightBox.left + 20 },
    ];
    const placed = placeNodeLabels([node], options({ obstacles }));
    expect(placed.get("a")?.halo).toBe(true);
    const cost = (side: (typeof sides)[number]) =>
      obstacles.reduce(
        (total, obstacle) =>
          total + overlapArea(labelBox(node, side, metrics), obstacle),
        0,
      );
    const cheapest = Math.min(...sides.map(cost));
    expect(cost(placed.get("a")!.placement)).toBe(cheapest);
  });

  it("keeps labels inside the canvas bounds", () => {
    // Below and above are taken; right would run off the canvas edge.
    const node = candidate({ id: "a", x: 980, y: 100, text: "Edge node" });
    const metrics = { fontSize: FONT_SIZE, zoom: 1 };
    const placed = placeNodeLabels(
      [node],
      options({
        // Narrow blockers straight under and over the node.
        obstacles: [
          { ...labelBox(node, "below", metrics), left: 970, right: 990 },
          { ...labelBox(node, "above", metrics), left: 970, right: 990 },
        ],
        bounds: { left: 0, top: 0, right: 1000, bottom: 1000 },
      }),
    );
    expect(placed.get("a")).toMatchObject({ placement: "left", halo: false });
  });

  it("places priority labels (hovered, selected, cited) first, ahead of higher-degree ones", () => {
    const nodes = [
      candidate({ id: "hub", x: 100, y: 100, text: "Hub label", degree: 9 }),
      candidate({ id: "leaf", x: 110, y: 100, text: "Leaf label" }),
    ];
    expect(placeNodeLabels(nodes, options()).get("hub")?.placement).toBe(
      "below",
    );
    const placed = placeNodeLabels(
      nodes,
      options({ priority: new Set(["leaf"]) }),
    );
    expect(placed.get("leaf")?.placement).toBe("below");
    expect(placed.get("hub")?.placement).not.toBe("below");
  });

  it("prefers emphasized labels (search matches, focus neighbors) over higher-degree ones", () => {
    const nodes = [
      candidate({ id: "hub", x: 100, y: 100, text: "Hub label", degree: 9 }),
      candidate({ id: "match", x: 105, y: 100, text: "Match label" }),
    ];
    const placed = placeNodeLabels(
      nodes,
      options({ emphasized: new Set(["match"]) }),
    );
    expect(placed.get("match")?.placement).toBe("below");
    expect(placed.get("hub")?.placement).not.toBe("below");
  });

  it("re-places labels in screen space, so zooming in separates crowded labels", () => {
    const nodes = [
      candidate({ id: "a", x: 100, y: 100, text: "Left label", degree: 2 }),
      candidate({ id: "b", x: 170, y: 100, text: "Right label" }),
    ];
    expect(placeNodeLabels(nodes, options()).get("b")?.placement).not.toBe(
      "below",
    );
    const zoomed = placeNodeLabels(nodes, options({ zoom: 2 }));
    expect(zoomed.get("a")?.placement).toBe("below");
    expect(zoomed.get("b")?.placement).toBe("below");
  });

  it("sizes label boxes with the supplied text measurement when given one", () => {
    const nodes = [
      candidate({ id: "a", x: 100, y: 100, text: "Short", degree: 2 }),
      candidate({ id: "b", x: 300, y: 100, text: "Short" }),
    ];
    expect(placeNodeLabels(nodes, options()).get("b")?.placement).toBe("below");
    expect(
      placeNodeLabels(nodes, options({ measureText: () => 400 })).get("b")
        ?.placement,
    ).not.toBe("below");
  });

  it("breaks degree ties by input order so the result is stable", () => {
    const nodes = [
      candidate({ id: "first", x: 100, y: 100, text: "Same row" }),
      candidate({ id: "second", x: 104, y: 100, text: "Same row" }),
    ];
    const placed = placeNodeLabels(nodes, options());
    expect(placed.get("first")?.placement).toBe("below");
    expect([...placeNodeLabels(nodes, options()).entries()]).toEqual([
      ...placed.entries(),
    ]);
  });

  it("labels every node in a dense graph, overlapping only where a halo is drawn", () => {
    // Deterministic pseudo-random cloud (linear congruential generator).
    let seed = 7;
    const next = () => {
      seed = (seed * 1_103_515_245 + 12_345) % 2_147_483_648;
      return seed / 2_147_483_648;
    };
    const nodes = Array.from({ length: 300 }, (_, index) =>
      candidate({
        id: `n${String(index)}`,
        x: next() * 1560,
        y: next() * 1120,
        radius: 12 + next() * 18,
        text: "x".repeat(4 + Math.floor(next() * 23)),
        degree: Math.floor(next() * 20),
      }),
    );
    const placed = placeNodeLabels(nodes, options());
    expect(placed.size).toBe(nodes.length);
    const boxes = boxesOf(nodes, placed);
    const clean = nodes.filter((node) => !placed.get(node.id)!.halo);
    expect(clean.length).toBeGreaterThan(0);
    // A label placed without a halo overlaps nothing placed before it, so
    // no two halo-free labels may overlap each other.
    expectNoOverlaps(clean.map((node) => boxes.get(node.id)!));
  });
});

describe("placeGraphLabels", () => {
  // A hub whose four short edges put an edge label on every side of it.
  const hub = candidate({ id: "hub", x: 300, y: 300, text: "Hub" });
  const spokes = [
    { id: "down", x2: 300, y2: 400 },
    { id: "up", x2: 300, y2: 200 },
    { id: "right", x2: 400, y2: 300 },
    { id: "left", x2: 200, y2: 300 },
  ];
  const ends = spokes.map((spoke) =>
    candidate({ id: `end-${spoke.id}`, x: spoke.x2, y: spoke.y2, text: "E" }),
  );
  const edges = spokes.map((spoke) => ({
    id: spoke.id,
    x1: 300,
    y1: 300,
    x2: spoke.x2,
    y2: spoke.y2,
    text: "requires",
  }));

  it("draws edge labels at their edge midpoints when nothing is in the way", () => {
    // (The horizontal spokes' pills are wider than half their edge, so they
    // slide off the end node's circle; the vertical ones fit at the middle.)
    const placed = placeGraphLabels(ends, edges, options());
    expect(placed.edges.get("down")).toEqual({ x: 300, y: 350 });
    expect(placed.edges.get("up")).toEqual({ x: 300, y: 250 });
  });

  it("slides an edge label along its edge out of a priority node label's way", () => {
    const nodes = [hub, ...ends];
    const placed = placeGraphLabels(
      nodes,
      edges,
      options({ priority: new Set(["hub"]) }),
    );
    const hubLabel = placed.nodes.get("hub")!;
    expect(hubLabel).toMatchObject({ placement: "below", halo: false });
    const hubBox = labelBox(hub, "below", { fontSize: FONT_SIZE, zoom: 1 });
    const down = placed.edges.get("down")!;
    expect(down).not.toEqual({ x: 300, y: 350 });
    expect(down.x).toBe(300);
    expect(
      overlapArea(edgeLabelBox(down.x, down.y, "requires", 1), hubBox),
    ).toBe(0);
  });

  it("moves an edge label beside an edge too short to hold it between its nodes", () => {
    const a = candidate({ id: "a", x: 100, y: 100, radius: 14, text: "A" });
    const b = candidate({ id: "b", x: 170, y: 100, radius: 14, text: "B" });
    const placed = placeGraphLabels(
      [a, b],
      [{ id: "ab", x1: 100, y1: 100, x2: 170, y2: 100, text: "requires" }],
      options(),
    );
    const at = placed.edges.get("ab")!;
    expect(at.x).toBeCloseTo(135);
    expect(at.y).not.toBeCloseTo(100);
    const box = edgeLabelBox(at.x, at.y, "requires", 1);
    for (const node of [a, b]) {
      const nearestX = Math.min(Math.max(node.x, box.left), box.right);
      const nearestY = Math.min(Math.max(node.y, box.top), box.bottom);
      expect(
        (nearestX - node.x) ** 2 + (nearestY - node.y) ** 2,
      ).toBeGreaterThanOrEqual(node.radius ** 2);
    }
  });

  it("keeps edge labels off each other", () => {
    // Two edges crossing at the same midpoint.
    const placed = placeGraphLabels(
      [],
      [
        { id: "h", x1: 0, y1: 200, x2: 400, y2: 200, text: "requires" },
        { id: "v", x1: 200, y1: 0, x2: 200, y2: 400, text: "requires" },
      ],
      options(),
    );
    const box = (id: string) => {
      const at = placed.edges.get(id)!;
      return edgeLabelBox(at.x, at.y, "requires", 1);
    };
    expect(placed.edges.get("h")).toEqual({ x: 200, y: 200 });
    expect(overlapArea(box("h"), box("v"))).toBe(0);
  });

  it("keeps other labels off the edge labels", () => {
    const placed = placeGraphLabels([hub, ...ends], edges, options());
    const edgeBoxes = [...placed.edges.entries()].map(([id, at]) =>
      edgeLabelBox(at.x, at.y, edges.find((edge) => edge.id === id)!.text, 1),
    );
    for (const node of ends) {
      const label = placed.nodes.get(node.id)!;
      expect(label.halo).toBe(false);
      const box = labelBox(node, label.placement, {
        fontSize: FONT_SIZE,
        zoom: 1,
      });
      for (const edgeBox of edgeBoxes)
        expect(overlapArea(box, edgeBox)).toBe(0);
    }
  });
});
