import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import type { GraphEdge } from "../model/graph";

self.onmessage = (
  event: MessageEvent<{
    nodes: { id: string }[];
    edges: GraphEdge[];
    reducedMotion: boolean;
  }>,
) => {
  const graph = new Graph();
  event.data.nodes.forEach((node, index) => {
    const angle = (index / Math.max(event.data.nodes.length, 1)) * Math.PI * 2;
    graph.addNode(node.id, { x: Math.cos(angle), y: Math.sin(angle) });
  });
  event.data.edges.forEach((edge) => {
    if (graph.hasNode(edge.sourceNodeId) && graph.hasNode(edge.targetNodeId)) {
      graph.addEdgeWithKey(edge.edgeId, edge.sourceNodeId, edge.targetNodeId);
    }
  });
  if (!event.data.reducedMotion && graph.order > 1) {
    forceAtlas2.assign(graph, { iterations: Math.min(100, graph.order * 2) });
  }
  self.postMessage(
    graph.nodes().map((id) => ({
      id,
      x: graph.getNodeAttribute(id, "x"),
      y: graph.getNodeAttribute(id, "y"),
    })),
  );
};
