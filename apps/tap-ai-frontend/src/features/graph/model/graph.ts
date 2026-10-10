import type { components } from "../../../shared/api/generated/schema";

export type GraphProject = components["schemas"]["ProjectGraphView"];
export type GraphCommunity = components["schemas"]["ProjectGraphCommunityView"];
export type GraphFragmentStatus = "EXTRACTING" | "PARTIAL" | "FAILED";
export type GraphNode = components["schemas"]["ProjectGraphNodeView"];
export type GraphEdge = components["schemas"]["ProjectGraphEdgeView"];
export type GraphSubgraph = components["schemas"]["ProjectGraphSubgraphView"];
export type GraphNodeDetail =
  components["schemas"]["ProjectGraphNodeDetailView"];
export type GraphNodeSource =
  components["schemas"]["ProjectGraphSourceGroupView"];
export type GraphNodeRelation =
  components["schemas"]["ProjectGraphRelationGroupView"];

export const MAX_GRAPH_NODES = 500;
export const OVERVIEW_PAGE = 150;

export function boundedGraph(graph: GraphSubgraph): GraphSubgraph {
  const nodes = graph.nodes.slice(0, MAX_GRAPH_NODES);
  const identities = new Set(nodes.map((node) => node.nodeId));
  return {
    ...graph,
    nodes,
    edges: graph.edges.filter(
      (edge) =>
        identities.has(edge.sourceNodeId) && identities.has(edge.targetNodeId),
    ),
  };
}
