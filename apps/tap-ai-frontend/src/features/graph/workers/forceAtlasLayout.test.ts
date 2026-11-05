import { describe, expect, it } from "vitest";
import type { GraphEdge } from "../model/graph";
import { computeForceAtlasPositions } from "./forceAtlasLayout";

function edge(
  edgeId: string,
  sourceNodeId: string,
  targetNodeId: string,
): GraphEdge {
  return {
    edgeId,
    sourceNodeId,
    targetNodeId,
    relationType: "RELATED_TO",
    relationLabel: "related to",
    origin: "EXTRACTED",
    confidence: 0.9,
  };
}

describe("computeForceAtlasPositions", () => {
  const nodes = [
    { id: "n1", communityId: "alpha" },
    { id: "n2", communityId: "alpha" },
    { id: "n3", communityId: "alpha" },
  ];

  it("does not throw and returns every node's position when a pair of nodes has multiple (parallel) edges", () => {
    const edges = [
      edge("e1", "n1", "n2"),
      // Same pair, a second relation (the v9 overview evidence: both
      // EXCEPTION_OF and TRIGGERS between the same two nodes).
      edge("e2", "n1", "n2"),
    ];

    expect(() => {
      const positions = computeForceAtlasPositions(
        nodes,
        edges,
        ["alpha"],
        true,
      );
      expect(positions).toHaveLength(nodes.length);
      expect(new Set(positions.map((p) => p.id))).toEqual(
        new Set(["n1", "n2", "n3"]),
      );
    }).not.toThrow();
  });

  // Guard, not a repro: graphology's directed-edge uniqueness check is
  // direction-sensitive even without `multi: true` (confirmed separately —
  // `addEdgeWithKey("e2", "n2", "n1")` after `addEdgeWithKey("e1", "n1",
  // "n2")` never throws), so this case never reproduced the crash. Kept to
  // document that reversed edges between the same pair stay correctly
  // handled alongside the `multi: true` change.
  it("does not throw when the same pair also has a reversed edge", () => {
    const edges = [edge("e1", "n1", "n2"), edge("e2", "n2", "n1")];

    expect(() => {
      const positions = computeForceAtlasPositions(
        nodes,
        edges,
        ["alpha"],
        true,
      );
      expect(positions).toHaveLength(nodes.length);
    }).not.toThrow();
  });

  it("ignores a duplicate edgeId instead of throwing", () => {
    const edges = [edge("e1", "n1", "n2"), edge("e1", "n1", "n3")];

    expect(() =>
      computeForceAtlasPositions(nodes, edges, ["alpha"], true),
    ).not.toThrow();
  });

  it("ignores a duplicate node id in the input instead of throwing", () => {
    const duplicateNodes = [...nodes, { id: "n1", communityId: "alpha" }];

    expect(() =>
      computeForceAtlasPositions(duplicateNodes, [], ["alpha"], true),
    ).not.toThrow();
  });

  it("runs force-atlas2 (reducedMotion=false) without throwing on parallel edges", () => {
    const edges = [edge("e1", "n1", "n2"), edge("e2", "n1", "n2")];

    expect(() =>
      computeForceAtlasPositions(nodes, edges, ["alpha"], false),
    ).not.toThrow();
  });
});
