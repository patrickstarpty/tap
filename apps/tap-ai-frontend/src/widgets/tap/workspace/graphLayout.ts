export type GraphCommunity =
  | "sources"
  | "application"
  | "underwriting"
  | "parties"
  | "testing"
  | "new-business"
  | "servicing"
  | "claims"
  | "codebase";
export type GraphNodeKind = "document" | "concept" | "entity";
export type GraphProvenance = "extracted" | "inferred";
export interface GraphNode {
  community: GraphCommunity;
  degree: number;
  id: string;
  kind: GraphNodeKind;
  label: string;
  secondary?: string;
  x: number;
  y: number;
}
export interface GraphEdge {
  id: string;
  label: string;
  provenance: GraphProvenance;
  source: string;
  target: string;
}
export const GRAPH_WIDTH = 1560;
export const GRAPH_HEIGHT = 1120;
export const COMMUNITY_ORDER: readonly GraphCommunity[] = [
  "sources",
  "new-business",
  "application",
  "underwriting",
  "servicing",
  "parties",
  "claims",
  "codebase",
  "testing",
];
// Shared categorical palette: swatches, nodes and edges use the same topic color.
export const COMMUNITY_COLORS: Record<GraphCommunity, string> = {
  sources: "#64748b",
  "new-business": "#2563eb",
  application: "#6366f1",
  underwriting: "#0d9488",
  servicing: "#d97706",
  parties: "#b86b48",
  claims: "#e05b73",
  codebase: "#8b5cf6",
  testing: "#0891b2",
};
