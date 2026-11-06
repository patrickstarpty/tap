import type { components } from "../../../shared/api/generated/schema";

type Node = components["schemas"]["WorkflowNodeView"];
type Edge = components["schemas"]["WorkflowEdgeView"];
export type WorkflowLayout = {
  layers: string[][];
  error: "cycle" | "missing_node" | null;
};

/** Stable directed layers; parent folding affects visibility, never dependency truth. */
export function layoutWorkflow(
  nodes: readonly Node[],
  edges: readonly Edge[],
  collapsedParents: ReadonlySet<string> = new Set(),
): WorkflowLayout {
  const byId = new Map(nodes.map((node) => [node.nodeId, node]));
  if (
    byId.size !== nodes.length ||
    edges.some((edge) => !byId.has(edge.fromNodeId) || !byId.has(edge.toNodeId))
  )
    return { layers: [], error: "missing_node" };
  const incoming = new Map(nodes.map((node) => [node.nodeId, 0]));
  const outgoing = new Map(nodes.map((node) => [node.nodeId, [] as string[]]));
  for (const edge of edges) {
    incoming.set(edge.toNodeId, incoming.get(edge.toNodeId)! + 1);
    outgoing.get(edge.fromNodeId)!.push(edge.toNodeId);
  }
  const queue = [...incoming]
    .filter(([, count]) => count === 0)
    .map(([id]) => id)
    .sort();
  const depth = new Map(nodes.map((node) => [node.nodeId, 0]));
  let visited = 0;
  while (queue.length > 0) {
    const id = queue.shift()!;
    visited += 1;
    for (const child of outgoing.get(id)!.sort()) {
      depth.set(child, Math.max(depth.get(child)!, depth.get(id)! + 1));
      const count = incoming.get(child)! - 1;
      incoming.set(child, count);
      if (count === 0) {
        queue.push(child);
        queue.sort();
      }
    }
  }
  if (visited !== nodes.length) return { layers: [], error: "cycle" };
  const hidden = (node: Node) => {
    const ancestors = new Set<string>();
    let parent = node.parentNodeId;
    while (parent !== null && byId.has(parent)) {
      if (collapsedParents.has(parent)) return true;
      if (ancestors.has(parent)) return true;
      ancestors.add(parent);
      parent = byId.get(parent)!.parentNodeId;
    }
    return false;
  };
  const layers: string[][] = [];
  for (const node of nodes.filter((item) => !hidden(item))) {
    const index = depth.get(node.nodeId)!;
    (layers[index] ??= []).push(node.nodeId);
  }
  return {
    layers: layers.filter(Boolean).map((layer) => layer.sort()),
    error: null,
  };
}
