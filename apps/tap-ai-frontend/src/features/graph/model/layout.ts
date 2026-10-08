import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "./graph";
import {
  scaleToCanvas,
  seedPositions,
  type LayoutPosition,
} from "./layoutGeometry";

export type { LayoutPosition };
export { scaleToCanvas, seedPositions };

const CANVAS_WIDTH = 1560;
const CANVAS_HEIGHT = 1120;

function toSeeds(
  nodes: GraphNode[],
): { id: string; communityId: string }[] {
  return nodes.map((node) => ({
    id: node.nodeId,
    communityId: node.community ?? "",
  }));
}

function toPositionMap(
  positions: LayoutPosition[],
): Map<string, { x: number; y: number }> {
  return new Map(positions.map((position) => [position.id, position]));
}

function computeSeededLayout(
  nodes: GraphNode[],
  communityOrder: string[],
): Map<string, { x: number; y: number }> {
  const scaled = scaleToCanvas(
    seedPositions(toSeeds(nodes), communityOrder),
    CANVAS_WIDTH,
    CANVAS_HEIGHT,
  );
  return toPositionMap(scaled);
}

// Content signatures (not array identity) key the worker-respawn effect, so
// callers passing freshly derived arrays with unchanged content (e.g.
// `communities.map(c => c.id)` recomputed every render) do not cause the
// worker to be torn down and recreated on every render.
function nodesSignature(seeds: { id: string; communityId: string }[]): string {
  return seeds.map((seed) => `${seed.id}:${seed.communityId}`).join("|");
}

function edgesSignature(edges: GraphEdge[]): string {
  return edges.map((edge) => edge.edgeId).join("|");
}

function communityOrderSignature(communityOrder: string[]): string {
  return communityOrder.join("|");
}

export function useGraphLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  communityOrder: string[],
): Map<string, { x: number; y: number }> {
  const hasWorker = typeof Worker !== "undefined";
  const seeds = useMemo(() => toSeeds(nodes), [nodes]);
  // Pure fallback: computed on every render without touching state, so a
  // Worker-less environment (e.g. jsdom) never triggers a setState loop, and
  // every currently-known node always has a position even before the worker
  // (re)computes a refined layout for a grown node set.
  const seededFallback = useMemo(
    () => computeSeededLayout(nodes, communityOrder),
    [nodes, communityOrder],
  );
  const [workerPositions, setWorkerPositions] = useState<
    Map<string, { x: number; y: number }> | null
  >(null);

  const nodesSig = useMemo(() => nodesSignature(seeds), [seeds]);
  const edgesSig = useMemo(() => edgesSignature(edges), [edges]);
  const communitySig = useMemo(
    () => communityOrderSignature(communityOrder),
    [communityOrder],
  );

  // Read the latest inputs from a ref inside the effect, so the effect's own
  // dependency array can stay on content signatures instead of array
  // identities (fixes the respawn loop described in review I1).
  const latestInputs = useRef({ seeds, edges, communityOrder });
  latestInputs.current = { seeds, edges, communityOrder };

  useEffect(() => {
    if (!hasWorker) return;
    const worker = new Worker(
      new URL("../workers/forceAtlas.worker.ts", import.meta.url),
      { type: "module" },
    );
    worker.onmessage = (event: MessageEvent<LayoutPosition[]>) => {
      const scaled = scaleToCanvas(event.data, CANVAS_WIDTH, CANVAS_HEIGHT);
      setWorkerPositions(toPositionMap(scaled));
    };
    const current = latestInputs.current;
    worker.postMessage({
      nodes: current.seeds,
      edges: current.edges,
      communityOrder: current.communityOrder,
      reducedMotion:
        typeof window !== "undefined" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    });
    return () => worker.terminate();
  }, [hasWorker, nodesSig, edgesSig, communitySig]);

  return useMemo(() => {
    if (!hasWorker || !workerPositions) return seededFallback;
    // Overlay: the seeded fallback guarantees every current node has a
    // position immediately (e.g. right after "load more" adds nodes the
    // worker hasn't replied for yet); the worker's refined positions take
    // precedence for nodes it has already resolved, but only for nodes
    // still present in the current set (a stale worker reply for a node
    // that has since dropped out must not resurrect it).
    const merged = new Map(seededFallback);
    for (const [id, position] of workerPositions) {
      if (merged.has(id)) merged.set(id, position);
    }
    return merged;
  }, [hasWorker, seededFallback, workerPositions]);
}
