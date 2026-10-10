// Display-ready graph types consumed by `KnowledgeGraph.tsx`. Unlike the
// earlier fixed-topic fixture (`publishedGraphData.ts`, now deleted), the
// project graph has no closed set of communities or node kinds: both are
// server-driven (`features/graph/model/graph.ts`), so this module only
// carries the laid-out, display-ready shapes that `toOverviewData.ts`
// produces from the API response plus `useGraphLayout` positions.
export type GraphCommunity = string;
export type GraphProvenance = "extracted" | "inferred";

export interface GraphNode {
  id: string;
  label: string;
  nodeType: string;
  community: GraphCommunity;
  communityLabel: string;
  color: string;
  degree: number;
  aliases: string[];
  secondary?: string;
  x: number;
  y: number;
}

export interface GraphEdge {
  id: string;
  label: string;
  relationType: string;
  provenance: GraphProvenance;
  source: string;
  target: string;
}

export const GRAPH_WIDTH = 1560;
export const GRAPH_HEIGHT = 1120;
