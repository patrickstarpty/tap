import type { GraphSubgraph } from "../../../features/graph/model/graph";
import type { LibrarySource } from "./model";
import {
  GRAPH_HEIGHT,
  GRAPH_WIDTH,
  type GraphCommunity,
  type GraphEdge,
  type GraphNode,
} from "./knowledgeGraphData";

function sourceCommunity(name: string): GraphCommunity {
  const title = name.toLocaleLowerCase();
  if (/underwrit|risk|health|disclosure/.test(title)) return "underwriting";
  if (/beneficiar|party|approval|access/.test(title)) return "parties";
  if (/claim|incident/.test(title)) return "claims";
  if (/servic|retention/.test(title)) return "servicing";
  if (/test|quality|release/.test(title)) return "testing";
  if (/code|system/.test(title)) return "codebase";
  if (/procur|new.business/.test(title)) return "new-business";
  return "application";
}

/** Places published service nodes on the established Library graph canvas. */
export function publishedGraphData(
  graph: GraphSubgraph,
  source: LibrarySource,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const community = sourceCommunity(source.name);
  const center = { x: GRAPH_WIDTH / 2, y: GRAPH_HEIGHT / 2 };
  const documentNodeId = graph.nodes.find(
    (node) =>
      node.nodeType.toLocaleLowerCase() === "document" ||
      node.label.startsWith("rev_"),
  )?.nodeId;
  const otherNodes = graph.nodes.filter(
    (node) => node.nodeId !== documentNodeId,
  );
  const ids = new Map<string, string>();
  const nodes: GraphNode[] = graph.nodes.map((node) => {
    const document = node.nodeId === documentNodeId;
    const id = document ? `source-${source.id}` : `published-${node.nodeId}`;
    ids.set(node.nodeId, id);
    const index = otherNodes.findIndex((item) => item.nodeId === node.nodeId);
    const angle =
      -Math.PI / 2 + (index / Math.max(otherNodes.length, 1)) * Math.PI * 2;
    return {
      id,
      label: document ? source.name : node.label,
      secondary: document ? source.type : undefined,
      kind: document
        ? "document"
        : node.nodeType.toLocaleLowerCase() === "entity"
          ? "entity"
          : "concept",
      community: document ? "sources" : community,
      degree: 0,
      x: document
        ? center.x
        : Math.max(
            50,
            Math.min(GRAPH_WIDTH - 50, center.x + 350 * Math.cos(angle)),
          ),
      y: document
        ? center.y
        : Math.max(
            80,
            Math.min(GRAPH_HEIGHT - 80, center.y + 275 * Math.sin(angle)),
          ),
    };
  });
  const edges: GraphEdge[] = graph.edges.flatMap((edge) => {
    const sourceId = ids.get(edge.sourceNodeId);
    const targetId = ids.get(edge.targetNodeId);
    return sourceId && targetId
      ? [
          {
            id: edge.edgeId,
            source: sourceId,
            target: targetId,
            label: edge.relationType,
            provenance:
              edge.origin === "EXTRACTED"
                ? ("extracted" as const)
                : ("inferred" as const),
          },
        ]
      : [];
  });
  for (const edge of edges) {
    nodes.find((node) => node.id === edge.source)!.degree++;
    nodes.find((node) => node.id === edge.target)!.degree++;
  }
  return { nodes, edges };
}
