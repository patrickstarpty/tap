import type { GraphOverview } from "../../../features/graph/api/client";
import type { LibrarySource } from "./model";
import {
  GRAPH_HEIGHT,
  GRAPH_WIDTH,
  type GraphEdge,
  type GraphNode,
} from "./graphLayout";
import { sourceCommunity } from "./publishedGraphData";

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

/** Places merged overview nodes so the most connected sit at the center. */
export function overviewGraphData(
  overview: GraphOverview,
  sources: readonly LibrarySource[],
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const names = new Map(sources.map((source) => [source.id, source.name]));
  const center = { x: GRAPH_WIDTH / 2, y: GRAPH_HEIGHT / 2 };
  const count = Math.max(overview.nodes.length, 1);
  const nodes: GraphNode[] = overview.nodes.map((node, index) => {
    const spread = Math.sqrt((index + 0.5) / count);
    const angle = index * GOLDEN_ANGLE;
    const firstSource = node.members[0]?.sourceId;
    return {
      id: node.overviewNodeId,
      label: node.label,
      kind: node.nodeType === "ENTITY" ? "entity" : "concept",
      community: sourceCommunity((firstSource && names.get(firstSource)) ?? ""),
      degree: 0,
      x: Math.max(
        50,
        Math.min(GRAPH_WIDTH - 50, center.x + 680 * spread * Math.cos(angle)),
      ),
      y: Math.max(
        80,
        Math.min(GRAPH_HEIGHT - 80, center.y + 460 * spread * Math.sin(angle)),
      ),
    };
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const edges: GraphEdge[] = overview.edges.flatMap((edge) => {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) return [];
    source.degree++;
    target.degree++;
    return [
      {
        id: edge.overviewEdgeId,
        source: edge.source,
        target: edge.target,
        label: edge.relationType,
      },
    ];
  });
  return { nodes, edges };
}
