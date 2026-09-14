export type GraphOrigin = "EXTRACTED" | "INFERRED";

export interface GraphNode {
  nodeId: string;
  label: string;
  nodeType: string;
  canonicalKey: string;
  community?: string;
  evidenceIds?: string[];
}

export interface GraphEdge {
  edgeId: string;
  sourceNodeId: string;
  targetNodeId: string;
  relationType: string;
  origin: GraphOrigin;
  confidence: number;
  evidenceIds?: string[];
}

export interface GraphEvidence {
  evidenceId: string;
  sourceRevisionId: string;
  documentRevisionId: string;
  chunkId: string;
  anchor: Record<string, unknown>;
  contentDigest: string;
}

export interface GraphSubgraph {
  snapshotId: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  evidence?: GraphEvidence[];
}

export interface GraphEvidenceLink {
  evidenceId: string;
  label: string;
  href: string;
}

export const MAX_GRAPH_NODES = 500;

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
