import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import type { GraphEdge } from "../model/graph";
import { seedPositions } from "../model/layoutGeometry";

self.onmessage = (
  event: MessageEvent<{
    nodes: { id: string; communityId: string }[];
    edges: GraphEdge[];
    communityOrder: string[];
    reducedMotion: boolean;
  }>,
) => {
  const graph = new Graph();
  const seeded = seedPositions(event.data.nodes, event.data.communityOrder);
  const seededById = new Map(seeded.map((position) => [position.id, position]));
  event.data.nodes.forEach((node) => {
    const position = seededById.get(node.id) ?? { x: 0, y: 0 };
    graph.addNode(node.id, { x: position.x, y: position.y });
  });
  event.data.edges.forEach((edge) => {
    if (graph.hasNode(edge.sourceNodeId) && graph.hasNode(edge.targetNodeId)) {
      graph.addEdgeWithKey(edge.edgeId, edge.sourceNodeId, edge.targetNodeId);
    }
  });
  if (!event.data.reducedMotion && graph.order > 1) {
    forceAtlas2.assign(graph, {
      iterations: Math.min(150, graph.order * 2),
    });
  }
  self.postMessage(
    graph.nodes().map((id) => ({
      id,
      x: graph.getNodeAttribute(id, "x"),
      y: graph.getNodeAttribute(id, "y"),
    })),
  );
};
