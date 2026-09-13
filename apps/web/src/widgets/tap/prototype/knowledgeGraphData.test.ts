import { describe, expect, it } from "vitest";
import { PROTOTYPE_COPY } from "./copy";
import { FWD_REPRESENTATIVE_SOURCES, FWD_KNOWLEDGE } from "./fwdKnowledge";
import { SAMPLE_FILES } from "./sampleFiles";
import { buildKnowledgeGraph } from "./knowledgeGraphData";

describe("knowledge graph relationships", () => {
  it("exposes business and codebase records in their own topic groups", () => {
    const data = buildKnowledgeGraph(
      PROTOTYPE_COPY.en,
      FWD_REPRESENTATIVE_SOURCES,
    );
    for (const [id, group] of [
      ["nb-issue", "new-business"],
      ["ps-beneficiary", "servicing"],
      ["cl-medical", "claims"],
      ["system-policy", "codebase"],
      ["code-beneficiary", "codebase"],
    ]) {
      expect(
        data.nodes.find((node) => node.id === `source-fwd-${id}`)?.community,
      ).toBe(group);
      expect(
        data.nodes.some(
          (node) => node.kind === "concept" && node.community === group,
        ),
      ).toBe(true);
    }
  });
  it("connects documents to their subject instead of a single application hub", () => {
    const data = buildKnowledgeGraph(PROTOTYPE_COPY.en, SAMPLE_FILES);
    const targets = (id: string) =>
      data.edges
        .filter((e) => e.source === `source-${id}`)
        .map((e) => e.target);
    expect(targets("sample-underwriting")).toEqual([
      "underwriting",
      "risk-assessment",
    ]);
    expect(targets("sample-beneficiary")).toEqual(["beneficiary", "approval"]);
    expect(targets("sample-test-cases")).toEqual(["test-cases", "allocation"]);
    expect(targets("sample-log")).toEqual(["execution", "defect"]);
    expect(new Set(data.edges.map((e) => e.label)).size).toBeGreaterThan(8);
  });
  it("adds a representative from every FWD domain and preserves their traceability", () => {
    expect(FWD_REPRESENTATIVE_SOURCES).toHaveLength(10);
    const selectedIds = new Set(
      FWD_REPRESENTATIVE_SOURCES.map((source) => source.id.slice(4)),
    );
    const groups = new Set(
      FWD_KNOWLEDGE.nodes
        .filter((node) => selectedIds.has(node.id))
        .map((node) => node.group),
    );
    expect(groups.size).toBe(9);
    const data = buildKnowledgeGraph(PROTOTYPE_COPY.en, [
      ...SAMPLE_FILES,
      ...FWD_REPRESENTATIVE_SOURCES,
    ]);
    const hasLink = (source: string, target: string) =>
      data.edges.some(
        (edge) =>
          edge.source === `source-fwd-${source}` &&
          edge.target === `source-fwd-${target}`,
      );
    expect(hasLink("ps-beneficiary", "code-beneficiary")).toBe(true);
    expect(hasLink("test-beneficiary-retry", "automation-beneficiary")).toBe(
      true,
    );
    expect(hasLink("automation-beneficiary", "run-servicing")).toBe(true);
    expect(hasLink("run-servicing", "defect-duplicate")).toBe(true);
    const filtered = buildKnowledgeGraph(
      PROTOTYPE_COPY.en,
      FWD_REPRESENTATIVE_SOURCES.filter((source) => source.type === "TS"),
    );
    const ids = new Set(filtered.nodes.map((node) => node.id));
    expect(
      filtered.edges.every(
        (edge) => ids.has(edge.source) && ids.has(edge.target),
      ),
    ).toBe(true);
  });
  it("keeps every example separate and all relationships connected to real nodes", () => {
    const data = buildKnowledgeGraph(PROTOTYPE_COPY.en, [
      ...SAMPLE_FILES,
      ...FWD_REPRESENTATIVE_SOURCES,
    ]);
    const ids = new Set(data.nodes.map((n) => n.id));
    expect(data.nodes.filter((n) => n.kind === "document")).toHaveLength(28);
    for (const edge of data.edges) {
      expect(ids.has(edge.source)).toBe(true);
      expect(ids.has(edge.target)).toBe(true);
    }
    for (let i = 0; i < data.nodes.length; i++)
      for (let j = i + 1; j < data.nodes.length; j++) {
        expect(
          Math.hypot(
            data.nodes[i]!.x - data.nodes[j]!.x,
            data.nodes[i]!.y - data.nodes[j]!.y,
          ),
        ).toBeGreaterThan(55);
      }
  });
});
