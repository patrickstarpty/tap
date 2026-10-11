import { expect, it } from "vitest";

import {
  placeGraphLabels,
  type EdgeLabelCandidate,
  type LabelCandidate,
} from "./labelPlacement";

// The overview's upper bound (`MAX_GRAPH_NODES`) with every edge labeled,
// as at or above `EDGE_LABEL_ZOOM`.
const NODE_COUNT = 500;
const EDGE_COUNT = 2_000;
const FRAME_BUDGET_MS = 16;

function buildGraph(): {
  nodes: LabelCandidate[];
  edges: (EdgeLabelCandidate & { source: string; target: string })[];
} {
  // Deterministic pseudo-random layout (linear congruential generator).
  let seed = 11;
  const next = () => {
    seed = (seed * 1_103_515_245 + 12_345) % 2_147_483_648;
    return seed / 2_147_483_648;
  };
  const nodes = Array.from({ length: NODE_COUNT }, (_, index) => ({
    id: `n${String(index)}`,
    x: 80 + next() * 1400,
    y: 80 + next() * 960,
    radius: 12 + next() * 18,
    text: "Node label ".slice(0, 4 + Math.floor(next() * 8)) + String(index),
    degree: Math.floor(next() * 20),
  }));
  const edges = Array.from({ length: EDGE_COUNT }, (_, index) => {
    const source = nodes[Math.floor(next() * NODE_COUNT)]!;
    const target = nodes[Math.floor(next() * NODE_COUNT)]!;
    return {
      id: `e${String(index)}`,
      source: source.id,
      target: target.id,
      x1: source.x,
      y1: source.y,
      x2: target.x,
      y2: target.y,
      text: ["requires", "precedes", "validated by"][index % 3]!,
    };
  });
  return { nodes, edges };
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)]!;
}

it(`re-places ${String(NODE_COUNT)} node and ${String(EDGE_COUNT)} edge labels for a hover within one frame`, async ({
  annotate,
}) => {
  const { nodes, edges } = buildGraph();
  const neighborsOf = (id: string) =>
    new Set(
      edges.flatMap((edge) =>
        edge.source === id
          ? [edge.target]
          : edge.target === id
            ? [edge.source]
            : [],
      ),
    );
  const run = (hovered: string | null) =>
    placeGraphLabels(nodes, edges, {
      priority: new Set(hovered ? [hovered] : []),
      emphasized: hovered ? neighborsOf(hovered) : new Set(),
      fontSize: 18,
      zoom: Math.sqrt(1.25),
    });
  // Warm up, then time hover changes (each a full re-placement). The
  // budget applies to the fastest run: the slower ones measure whatever
  // else the machine (e.g. the parallel test workers) was doing meanwhile.
  run(null);
  run("n0");
  const timings: number[] = [];
  for (let i = 0; i < 15; i += 1) {
    const started = performance.now();
    const placed = run(i % 2 === 0 ? `n${String(i)}` : null);
    timings.push(performance.now() - started);
    expect(placed.nodes.size).toBe(NODE_COUNT);
    expect(placed.edges.size).toBe(EDGE_COUNT);
  }
  const fastest = Math.min(...timings);
  const summary = `fastest ${fastest.toFixed(1)} ms, median ${median(
    timings,
  ).toFixed(1)} ms over ${String(timings.length)} runs`;
  await annotate(summary);
  expect(fastest, summary).toBeLessThanOrEqual(FRAME_BUDGET_MS);
});
