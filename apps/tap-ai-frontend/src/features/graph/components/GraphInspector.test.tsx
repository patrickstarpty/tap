import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GraphInspector } from "./GraphInspector";

describe("GraphInspector", () => {
  it("shows non-color provenance and an Evidence deep link", () => {
    render(
      <GraphInspector
        node={{
          nodeId: "node-1",
          label: "Policy",
          nodeType: "ENTITY",
          canonicalKey: "policy",
          community: "Governance",
        }}
        relations={[
          {
            edgeId: "edge-1",
            sourceNodeId: "node-1",
            targetNodeId: "node-2",
            relationType: "GOVERNS",
            origin: "EXTRACTED",
            confidence: 1,
          },
        ]}
        evidence={[
          {
            evidenceId: "evidence-1",
            label: "Handbook §2",
            href: "/api/v1/projects/tapper-demo/knowledge/graph/evidence/evidence-1",
          },
        ]}
      />,
    );
    expect(screen.getByText("EXTRACTED")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Handbook §2" })).toHaveAttribute(
      "href",
      expect.stringContaining("evidence-1"),
    );
  });
});
