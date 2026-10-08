import { useEffect, useMemo, useState } from "react";
import type { GraphEdge, GraphNode } from "./graph";

export type LayoutPosition = { id: string; x: number; y: number };

const COMMUNITY_RADIUS = 0.6;
const NODE_RADIUS = 0.15;
const CANVAS_WIDTH = 1560;
const CANVAS_HEIGHT = 1120;

function groupByCommunity(
  nodes: { id: string; communityId: string }[],
): Map<string, { id: string; communityId: string }[]> {
  const groups = new Map<string, { id: string; communityId: string }[]>();
  for (const node of nodes) {
    const members = groups.get(node.communityId) ?? [];
    members.push(node);
    groups.set(node.communityId, members);
  }
  return groups;
}

function communityCenters(
  communityOrder: string[],
): Map<string, { x: number; y: number }> {
  const centers = new Map<string, { x: number; y: number }>();
  communityOrder.forEach((communityId, index) => {
    if (communityOrder.length <= 1) {
      centers.set(communityId, { x: 0, y: 0 });
      return;
    }
    const angle = (index / communityOrder.length) * Math.PI * 2;
    centers.set(communityId, {
      x: Math.cos(angle) * COMMUNITY_RADIUS,
      y: Math.sin(angle) * COMMUNITY_RADIUS,
    });
  });
  return centers;
}

export function seedPositions(
  nodes: { id: string; communityId: string }[],
  communityOrder: string[],
): LayoutPosition[] {
  const groups = groupByCommunity(nodes);
  const order =
    communityOrder.length > 0 ? communityOrder : [...groups.keys()];
  const centers = communityCenters(order);

  const positions: LayoutPosition[] = [];
  for (const [communityId, members] of groups) {
    const center = centers.get(communityId) ?? { x: 0, y: 0 };
    members.forEach((member, index) => {
      const angle = (index / Math.max(members.length, 1)) * Math.PI * 2;
      positions.push({
        id: member.id,
        x: center.x + Math.cos(angle) * NODE_RADIUS,
        y: center.y + Math.sin(angle) * NODE_RADIUS,
      });
    });
  }
  return positions;
}

function scaleAxis(
  value: number,
  min: number,
  max: number,
  dimension: number,
  margin: number,
): number {
  if (max === min) return dimension / 2;
  return margin + ((value - min) / (max - min)) * (dimension - margin * 2);
}

export function scaleToCanvas(
  positions: LayoutPosition[],
  width: number,
  height: number,
  margin = 80,
): LayoutPosition[] {
  if (positions.length === 0) return [];
  const xs = positions.map((position) => position.x);
  const ys = positions.map((position) => position.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  return positions.map((position) => ({
    id: position.id,
    x: scaleAxis(position.x, minX, maxX, width, margin),
    y: scaleAxis(position.y, minY, maxY, height, margin),
  }));
}

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

export function useGraphLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  communityOrder: string[],
): Map<string, { x: number; y: number }> {
  const hasWorker = typeof Worker !== "undefined";
  const seeds = useMemo(() => toSeeds(nodes), [nodes]);
  // Pure fallback: computed on every render without touching state, so a
  // Worker-less environment (e.g. jsdom) never triggers a setState loop.
  const seededFallback = useMemo(
    () => computeSeededLayout(nodes, communityOrder),
    [nodes, communityOrder],
  );
  const [workerPositions, setWorkerPositions] = useState<
    Map<string, { x: number; y: number }> | null
  >(null);

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
    worker.postMessage({
      nodes: seeds,
      edges,
      communityOrder,
      reducedMotion:
        typeof window !== "undefined" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    });
    return () => worker.terminate();
  }, [hasWorker, seeds, edges, communityOrder]);

  if (!hasWorker) return seededFallback;
  return workerPositions ?? seededFallback;
}
