import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GraphEdge, GraphNode } from "./graph";
import { scaleToCanvas, seedPositions, useGraphLayout } from "./layout";

class FakeWorker {
  static instances: FakeWorker[] = [];
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  readonly posted: unknown[] = [];
  terminated = false;

  constructor(
    public readonly url: string | URL,
    public readonly options?: unknown,
  ) {
    FakeWorker.instances.push(this);
  }

  postMessage(data: unknown): void {
    this.posted.push(data);
  }

  terminate(): void {
    this.terminated = true;
  }
}

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
        const distance = Math.hypot(position.x - centerX, position.y - centerY);
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
        communityId: "alpha",
        degree: 1,
      },
      {
        nodeId: "n2",
        label: "Node 2",
        nodeType: "Entity",
        canonicalKey: "n2",
        communityId: "alpha",
        degree: 1,
      },
      {
        nodeId: "n3",
        label: "Node 3",
        nodeType: "Entity",
        canonicalKey: "n3",
        communityId: "beta",
        degree: 0,
      },
    ];
    const edges: GraphEdge[] = [
      {
        edgeId: "e1",
        sourceNodeId: "n1",
        targetNodeId: "n2",
        relationType: "RELATES_TO",
        relationLabel: "Relates to",
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

function buildNode(id: string, community: string): GraphNode {
  return {
    nodeId: id,
    label: id,
    nodeType: "Entity",
    canonicalKey: id,
    communityId: community,
    degree: 0,
  };
}

describe("useGraphLayout with a Worker", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    FakeWorker.instances = [];
  });

  it("keeps a single worker across re-renders with equal content and respawns only when content changes", () => {
    vi.stubGlobal("Worker", FakeWorker);

    const edges: GraphEdge[] = [
      {
        edgeId: "e1",
        sourceNodeId: "n1",
        targetNodeId: "n2",
        relationType: "RELATES_TO",
        relationLabel: "Relates to",
        origin: "EXTRACTED",
        confidence: 0.9,
      },
    ];

    const { rerender } = renderHook(
      ({
        nodes,
        edges,
        order,
      }: {
        nodes: GraphNode[];
        edges: GraphEdge[];
        order: string[];
      }) => useGraphLayout(nodes, edges, order),
      {
        initialProps: {
          nodes: [buildNode("n1", "alpha"), buildNode("n2", "alpha")],
          edges: [...edges],
          order: ["alpha"],
        },
      },
    );

    expect(FakeWorker.instances).toHaveLength(1);
    expect(FakeWorker.instances[0].posted).toHaveLength(1);

    // Re-render with brand-new array literals carrying the same content
    // (mirrors a caller deriving arrays inline on every render).
    rerender({
      nodes: [buildNode("n1", "alpha"), buildNode("n2", "alpha")],
      edges: [...edges],
      order: ["alpha"],
    });

    expect(FakeWorker.instances).toHaveLength(1);
    expect(FakeWorker.instances[0].posted).toHaveLength(1);
    expect(FakeWorker.instances[0].terminated).toBe(false);

    // Changing the actual content must still respawn the worker.
    rerender({
      nodes: [
        buildNode("n1", "alpha"),
        buildNode("n2", "alpha"),
        buildNode("n3", "alpha"),
      ],
      edges: [...edges],
      order: ["alpha"],
    });

    expect(FakeWorker.instances).toHaveLength(2);
    expect(FakeWorker.instances[0].terminated).toBe(true);
    expect(FakeWorker.instances[1].posted).toHaveLength(1);
  });

  it("returns positions for every current node immediately after growing the node list, even before the worker replies", () => {
    vi.stubGlobal("Worker", FakeWorker);

    const edges: GraphEdge[] = [];
    const initialNodes = [buildNode("n1", "alpha"), buildNode("n2", "alpha")];

    const { result, rerender } = renderHook(
      ({ nodes }: { nodes: GraphNode[] }) =>
        useGraphLayout(nodes, edges, ["alpha"]),
      { initialProps: { nodes: initialNodes } },
    );

    const worker = FakeWorker.instances[0];
    act(() => {
      worker.onmessage?.({
        data: [
          { id: "n1", x: 0, y: 0 },
          { id: "n2", x: 1, y: 1 },
        ],
      } as MessageEvent<unknown>);
    });

    expect(result.current.size).toBe(2);

    rerender({
      nodes: [...initialNodes, buildNode("n3", "alpha")],
    });

    expect(result.current.size).toBe(3);
    expect(result.current.has("n3")).toBe(true);
  });
});
