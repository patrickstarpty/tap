// Pure graphology wiring behind the force-atlas worker (`forceAtlas.worker.ts`),
// split out so it can be unit-tested directly in Vitest without a real
// `Worker` (jsdom has none) standing in the way.
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import type { GraphEdge } from "../model/graph";
import { seedPositions, type LayoutPosition } from "../model/layoutGeometry";

/**
 * Builds the layout graph (seeded on each node's community arc), refines it
 * with force-atlas2 unless the caller asked for reduced motion or there is
 * nothing to spread apart, and returns every node's resulting position.
 * Never throws on parallel or reversed edges between the same node pair.
 *
 * `multi: true` is required: the project graph routinely carries more than
 * one relation between the same pair of nodes (e.g. both `EXCEPTION_OF` and
 * `TRIGGERS` between two nodes) — a non-multi graph's `addEdgeWithKey`
 * throws `an edge linking "u" to "v" already exists` the moment a second
 * edge between an already-connected pair is added, even though each edge
 * has its own unique `edgeId` key. That throw happened inside the worker's
 * `onmessage` handler, uncaught, so the worker silently stopped producing
 * layout replies for the rest of the session (the canvas then only ever
 * showed the seeded fallback positions).
 *
 * Both `addNode` and `addEdgeWithKey` are additionally guarded against
 * duplicate ids/keys in the input data itself (not just duplicate node
 * pairs) — a duplicate would otherwise throw the same way and silently
 * drop the rest of the batch.
 */
export function computeForceAtlasPositions(
  nodes: { id: string; communityId: string }[],
  edges: GraphEdge[],
  communityOrder: string[],
  reducedMotion: boolean,
): LayoutPosition[] {
  const graph = new Graph({ multi: true });
  const seeded = seedPositions(nodes, communityOrder);
  const seededById = new Map(seeded.map((position) => [position.id, position]));
  nodes.forEach((node) => {
    if (graph.hasNode(node.id)) return;
    const position = seededById.get(node.id) ?? { x: 0, y: 0 };
    graph.addNode(node.id, { x: position.x, y: position.y });
  });
  edges.forEach((edge) => {
    if (
      !graph.hasNode(edge.sourceNodeId) ||
      !graph.hasNode(edge.targetNodeId) ||
      graph.hasEdge(edge.edgeId)
    ) {
      return;
    }
    graph.addEdgeWithKey(edge.edgeId, edge.sourceNodeId, edge.targetNodeId);
  });
  if (!reducedMotion && graph.order > 1) {
    forceAtlas2.assign(graph, {
      iterations: Math.min(150, graph.order * 2),
    });
  }
  return graph.nodes().map((id) => ({
    id,
    x: graph.getNodeAttribute(id, "x") as number,
    y: graph.getNodeAttribute(id, "y") as number,
  }));
}
