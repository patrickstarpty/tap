import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { GraphEdge, GraphNode } from "./graph";
import { scaleToCanvas, seedPositions, useGraphLayout } from "./layout";

function buildCommunityNodes(
  communityId: string,
  count: number,
): { id: string; communityId: string }[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `${communityId}-${index}`,
    communityId,
  }));
}

describe("seedPositions", () => {
  it("seeds communities on separate arcs and keeps members near their center", () => {
    const communityOrder = ["alpha", "beta", "gamma"];
    const nodes = communityOrder.flatMap((communityId) =>
      buildCommunityNodes(communityId, 4),
    );

    const positions = seedPositions(nodes, communityOrder);
    const byId = new Map(positions.map((position) => [position.id, position]));

    const centers = new Map<string, { x: number; y: number }>();
    for (const communityId of communityOrder) {
      const members = nodes.filter((node) => node.communityId === communityId);
      const centerX =
        members.reduce((sum, node) => sum + byId.get(node.id)!.x, 0) /
        members.length;
      const centerY =
        members.reduce((sum, node) => sum + byId.get(node.id)!.y, 0) /
        members.length;
      centers.set(communityId, { x: centerX, y: centerY });

      for (const member of members) {
        const position = byId.get(member.id)!;
        const distance = Math.hypot(
          position.x - centerX,
          position.y - centerY,
        );
        expect(distance).toBeLessThanOrEqual(0.2);
      }
    }

    for (let i = 0; i < communityOrder.length; i += 1) {
      for (let j = i + 1; j < communityOrder.length; j += 1) {
        const a = centers.get(communityOrder[i])!;
        const b = centers.get(communityOrder[j])!;
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        expect(distance).toBeGreaterThanOrEqual(0.8);
      }
    }
  });
});

describe("scaleToCanvas", () => {
  it("scales positions inside the canvas with margin", () => {
    const communityOrder = ["alpha", "beta", "gamma"];
    const nodes = communityOrder.flatMap((communityId) =>
      buildCommunityNodes(communityId, 4),
    );
    const seeded = seedPositions(nodes, communityOrder);

    const scaled = scaleToCanvas(seeded, 1560, 1120);

    for (const position of scaled) {
      expect(position.x).toBeGreaterThanOrEqual(80);
      expect(position.x).toBeLessThanOrEqual(1480);
      expect(position.y).toBeGreaterThanOrEqual(80);
      expect(position.y).toBeLessThanOrEqual(1040);
    }
  });
});

describe("useGraphLayout", () => {
  it("falls back to seeded positions without a Worker", () => {
    const nodes: GraphNode[] = [
      {
        nodeId: "n1",
        label: "Node 1",
        nodeType: "Entity",
        canonicalKey: "n1",
        community: "alpha",
      },
      {
        nodeId: "n2",
        label: "Node 2",
        nodeType: "Entity",
        canonicalKey: "n2",
        community: "alpha",
      },
      {
        nodeId: "n3",
        label: "Node 3",
        nodeType: "Entity",
        canonicalKey: "n3",
        community: "beta",
      },
    ];
    const edges: GraphEdge[] = [
      {
        edgeId: "e1",
        sourceNodeId: "n1",
        targetNodeId: "n2",
        relationType: "RELATES_TO",
        origin: "EXTRACTED",
        confidence: 0.9,
      },
    ];

    const { result } = renderHook(() =>
      useGraphLayout(nodes, edges, ["alpha", "beta"]),
    );

    expect(result.current.size).toBe(nodes.length);
  });
});
