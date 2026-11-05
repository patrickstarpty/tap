import { communityColor, OTHER_COMMUNITY_COLOR } from "../model/palette";
import type { GraphCommunity, GraphSubgraph } from "../model/graph";
import type {
  GraphEdge,
  GraphNode,
} from "../../../widgets/tap/workspace/graphLayout";

// Nodes whose `communityId` is missing, or names a community absent from the
// `GET /project` community list (e.g. a stale/renamed community), are never
// attributed to the graph's largest community — they fall into a dedicated
// "other" bucket instead, colored with the fixed `OTHER_COMMUNITY_COLOR`.
export const OTHER_COMMUNITY_ID = "__other__";

export interface OverviewCommunity {
  communityId: string;
  label: string;
  color: string;
  size: number;
}

export function toOverviewData(
  graph: GraphSubgraph,
  communities: readonly GraphCommunity[],
  positions: Map<string, { x: number; y: number }>,
  otherLabel: string,
): {
  nodes: GraphNode[];
  edges: GraphEdge[];
  communityOrder: OverviewCommunity[];
} {
  const orderedCommunities = [...communities].sort((a, b) => b.size - a.size);
  const colorIndexById = new Map(
    orderedCommunities.map((community, index) => [
      community.communityId,
      index,
    ]),
  );
  const labelById = new Map(
    orderedCommunities.map((community) => [
      community.communityId,
      community.label,
    ]),
  );

  let otherSize = 0;
  for (const node of graph.nodes) {
    if (node.communityId === undefined || node.communityId === null) {
      otherSize += 1;
      continue;
    }
    if (!colorIndexById.has(node.communityId)) otherSize += 1;
  }

  const communityOrder: OverviewCommunity[] = orderedCommunities.map(
    (community, index) => ({
      communityId: community.communityId,
      label: community.label,
      color: communityColor(index, false),
      size: community.size,
    }),
  );
  if (otherSize > 0) {
    communityOrder.push({
      communityId: OTHER_COMMUNITY_ID,
      label: otherLabel,
      color: OTHER_COMMUNITY_COLOR,
      size: otherSize,
    });
  }

  const nodes: GraphNode[] = graph.nodes.map((node) => {
    const communityId = node.communityId ?? undefined;
    const colorIndex =
      communityId === undefined ? undefined : colorIndexById.get(communityId);
    const isOther = colorIndex === undefined;
    const position = positions.get(node.nodeId) ?? { x: 0, y: 0 };
    return {
      id: node.nodeId,
      label: node.label,
      nodeType: node.nodeType,
      community: isOther ? OTHER_COMMUNITY_ID : communityId!,
      communityLabel: isOther
        ? otherLabel
        : (labelById.get(communityId!) ?? otherLabel),
      color: isOther
        ? OTHER_COMMUNITY_COLOR
        : communityColor(colorIndex, false),
      degree: node.degree,
      aliases: node.aliases ?? [],
      x: position.x,
      y: position.y,
    };
  });

  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges: GraphEdge[] = graph.edges
    .filter(
      (edge) =>
        nodeIds.has(edge.sourceNodeId) && nodeIds.has(edge.targetNodeId),
    )
    .map((edge) => ({
      id: edge.edgeId,
      source: edge.sourceNodeId,
      target: edge.targetNodeId,
      relationType: edge.relationType,
      label: edge.relationLabel || edge.relationType,
      provenance: edge.origin === "EXTRACTED" ? "extracted" : "inferred",
    }));

  return { nodes, edges, communityOrder };
}
