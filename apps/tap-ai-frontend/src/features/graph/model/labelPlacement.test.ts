import { describe, expect, it } from "vitest";

import {
  estimateTextWidth,
  labelBaseline,
  labelBox,
  placeNodeLabels,
  type LabelBox,
  type LabelCandidate,
} from "./labelPlacement";

const FONT_SIZE = 18;

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

function intersects(a: LabelBox, b: LabelBox): boolean {
  return (
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom
  );
}

function visibleBoxes(
  candidates: LabelCandidate[],
  placements: Map<string, "below" | "above">,
): LabelBox[] {
  return candidates.flatMap((item) => {
    const placement = placements.get(item.id);
    return placement ? [labelBox(item, placement, FONT_SIZE)] : [];
  });
}

function expectPairwiseDisjoint(boxes: LabelBox[]): void {
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      expect(intersects(boxes[i]!, boxes[j]!)).toBe(false);
    }
  }
}

const NO_IDS: ReadonlySet<string> = new Set();

describe("labelBaseline", () => {
  it("places a below label under the node and an above label over it", () => {
    const node = candidate({ y: 100, radius: 20 });
    expect(labelBaseline(node, "below")).toBeGreaterThan(100 + 20);
    const aboveBox = labelBox(node, "above", FONT_SIZE);
    expect(aboveBox.bottom).toBeLessThan(100 - 20);
  });
});

describe("labelBox", () => {
  it("is centered on the node and grows with the text length", () => {
    const short = labelBox(candidate({ x: 50, text: "Ab" }), "below", 18);
    const long = labelBox(
      candidate({ x: 50, text: "Abcdefghijkl" }),
      "below",
      18,
    );
    expect((short.left + short.right) / 2).toBeCloseTo(50);
    expect(long.right - long.left).toBeGreaterThan(short.right - short.left);
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
  it("sizes label boxes with the supplied text measurement when given one", () => {
    // Far enough apart that estimated widths fit side by side, but not
    // once the measurement reports each label as 400 units wide.
    const nodes = [
      candidate({ id: "a", x: 100, y: 100, text: "Short", degree: 2 }),
      candidate({ id: "b", x: 300, y: 100, text: "Short" }),
    ];
    const options = {
      pinned: NO_IDS,
      emphasized: NO_IDS,
      fontSize: FONT_SIZE,
    };
    expect(placeNodeLabels(nodes, options).get("b")).toBe("below");
    expect(
      placeNodeLabels(nodes, { ...options, measureText: () => 400 }).get("b"),
    ).toBe("above");
  });

  it("shows every label below its node when nothing collides", () => {
    const nodes = [
      candidate({ id: "a", x: 0, y: 0 }),
      candidate({ id: "b", x: 600, y: 0 }),
      candidate({ id: "c", x: 0, y: 400 }),
    ];
    const placements = placeNodeLabels(nodes, {
      pinned: NO_IDS,
      emphasized: NO_IDS,
      fontSize: FONT_SIZE,
    });
    expect([...placements.entries()].sort()).toEqual([
      ["a", "below"],
      ["b", "below"],
      ["c", "below"],
    ]);
  });

  it("moves a colliding label above its node instead of overlapping a neighbor", () => {
    // Two nodes side by side on the same row: their below labels overlap.
    const nodes = [
      candidate({ id: "policy", x: 100, y: 100, text: "Policy", degree: 3 }),
      candidate({
        id: "reinstatement",
        x: 150,
        y: 100,
        text: "Reinstatement",
        degree: 1,
      }),
    ];
    const placements = placeNodeLabels(nodes, {
      pinned: NO_IDS,
      emphasized: NO_IDS,
      fontSize: FONT_SIZE,
    });
    expect(placements.get("policy")).toBe("below");
    expect(placements.get("reinstatement")).toBe("above");
    expectPairwiseDisjoint(visibleBoxes(nodes, placements));
  });

  it("hides the lowest-degree label when neither slot is free", () => {
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
    const placements = placeNodeLabels(nodes, {
      pinned: NO_IDS,
      emphasized: NO_IDS,
      fontSize: FONT_SIZE,
    });
    expect(placements.get("renewal")).toBe("below");
    expect(placements.get("beneficiary")).toBe("above");
    expect(placements.has("address")).toBe(false);
    expectPairwiseDisjoint(visibleBoxes(nodes, placements));
  });

  it("always shows a pinned (hovered or selected) label, displacing what it collides with", () => {
    const nodes = [
      candidate({ id: "hub", x: 100, y: 100, text: "Hub label", degree: 9 }),
      candidate({ id: "leaf", x: 110, y: 100, text: "Leaf label" }),
    ];
    const unpinned = placeNodeLabels(nodes, {
      pinned: NO_IDS,
      emphasized: NO_IDS,
      fontSize: FONT_SIZE,
    });
    expect(unpinned.get("hub")).toBe("below");
    expect(unpinned.get("leaf")).not.toBe("below");

    const placements = placeNodeLabels(nodes, {
      pinned: new Set(["leaf"]),
      emphasized: NO_IDS,
      fontSize: FONT_SIZE,
    });
    expect(placements.get("leaf")).toBe("below");
    expect(placements.get("hub")).not.toBe("below");
    expectPairwiseDisjoint(visibleBoxes(nodes, placements));
  });

  it("keeps every pinned label even when pinned labels collide with each other", () => {
    const nodes = [
      candidate({ id: "a", x: 100, y: 100 }),
      candidate({ id: "b", x: 100, y: 100 }),
    ];
    const placements = placeNodeLabels(nodes, {
      pinned: new Set(["a", "b"]),
      emphasized: NO_IDS,
      fontSize: FONT_SIZE,
    });
    expect(placements.has("a")).toBe(true);
    expect(placements.has("b")).toBe(true);
  });

  it("prefers emphasized labels (highlighted path, search matches, focus neighbors) over higher-degree ones", () => {
    const nodes = [
      candidate({ id: "hub", x: 100, y: 100, text: "Hub label", degree: 9 }),
      candidate({ id: "cited", x: 105, y: 100, text: "Cited label" }),
    ];
    const placements = placeNodeLabels(nodes, {
      pinned: NO_IDS,
      emphasized: new Set(["cited"]),
      fontSize: FONT_SIZE,
    });
    expect(placements.get("cited")).toBe("below");
    expect(placements.get("hub")).not.toBe("below");
    expectPairwiseDisjoint(visibleBoxes(nodes, placements));
  });

  it("breaks degree ties by input order so the result is stable", () => {
    const nodes = [
      candidate({ id: "first", x: 100, y: 100, text: "Same row" }),
      candidate({ id: "second", x: 104, y: 100, text: "Same row" }),
      candidate({ id: "third", x: 108, y: 100, text: "Same row" }),
    ];
    const options = {
      pinned: NO_IDS,
      emphasized: NO_IDS,
      fontSize: FONT_SIZE,
    };
    const placements = placeNodeLabels(nodes, options);
    expect(placements.get("first")).toBe("below");
    expect(placements.get("second")).toBe("above");
    expect(placements.has("third")).toBe(false);
    expect([...placeNodeLabels(nodes, options).entries()]).toEqual([
      ...placements.entries(),
    ]);
  });

  it("never lets visible labels intersect in a dense graph", () => {
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
    const placements = placeNodeLabels(nodes, {
      pinned: new Set(["n5"]),
      emphasized: new Set(["n10", "n11"]),
      fontSize: FONT_SIZE,
    });
    expect(placements.has("n5")).toBe(true);
    expect(placements.size).toBeGreaterThan(0);
    expectPairwiseDisjoint(visibleBoxes(nodes, placements));
  });
});
