import { describe, expect, it } from "vitest";
import { FWD_KNOWLEDGE, getFwdNeighborhood } from "./fwdKnowledge";

describe("FWD HK demonstration knowledge", () => {
  it("keeps a connected, traceable lifecycle without presenting synthetic code as public fact", () => {
    const { nodes, edges } = FWD_KNOWLEDGE;
    const ids = new Set(nodes.map((n) => n.id));
    expect(ids.size).toBe(nodes.length);
    expect(nodes.length).toBeGreaterThan(200);
    expect(nodes.filter((n) => n.kind === "product").length).toBeGreaterThan(
      30,
    );
    expect(new Set(edges.map((e) => e.relation)).size).toBeGreaterThan(12);
    for (const edge of edges) {
      expect(ids.has(edge.source)).toBe(true);
      expect(ids.has(edge.target)).toBe(true);
    }
    for (const node of nodes) {
      expect(
        edges.some((e) => e.source === node.id || e.target === node.id),
      ).toBe(true);
      if (node.provenance === "public")
        expect(node.sourceUrl).toMatch(/^https:\/\/www.fwd.com.hk\//);
      if (
        [
          "system",
          "code",
          "test",
          "automation",
          "execution",
          "defect",
        ].includes(node.kind)
      ) {
        expect(node.provenance).toBe("demo");
        expect(node.content.length).toBeGreaterThan(80);
      }
    }
  });
  it("traverses the beneficiary change from business to implementation, automation and evidence", () => {
    const ids = getFwdNeighborhood(FWD_KNOWLEDGE, "ps-beneficiary", 4);
    for (const id of [
      "ps-beneficiary",
      "system-policy",
      "code-beneficiary",
      "test-beneficiary-retry",
      "automation-beneficiary",
      "run-servicing",
      "defect-duplicate",
    ]) {
      expect(ids.has(id)).toBe(true);
    }
    expect(getFwdNeighborhood(FWD_KNOWLEDGE, "missing", 1).size).toBe(0);
    expect(
      getFwdNeighborhood(FWD_KNOWLEDGE, "ps-beneficiary", 1).size,
    ).toBeLessThan(ids.size);
  });
});
