import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeGraphCanvas } from "./KnowledgeGraphCanvas";

const graph = {
  snapshotId: "snapshot-1",
  nodes: [
    {
      nodeId: "node-1",
      label: "Policy",
      nodeType: "ENTITY",
      canonicalKey: "policy",
      community: "Governance",
    },
    {
      nodeId: "node-2",
      label: "Claim",
      nodeType: "ENTITY",
      canonicalKey: "claim",
      community: "Claims",
    },
  ],
  edges: [
    {
      edgeId: "edge-1",
      sourceNodeId: "node-1",
      targetNodeId: "node-2",
      relationType: "GOVERNS",
      origin: "INFERRED" as const,
      confidence: 0.8,
    },
  ],
};

describe("KnowledgeGraphCanvas", () => {
  it("searches and filters the bounded graph without hiding provenance labels", () => {
    render(<KnowledgeGraphCanvas graph={graph} onSelectNode={vi.fn()} />);
    expect(screen.getByText("INFERRED")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "Policy" },
    });
    expect(screen.getByRole("button", { name: /Policy/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Claim/ }),
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Community"), {
      target: { value: "Claims" },
    });
    expect(screen.getByRole("button", { name: /Claim/ })).toBeInTheDocument();
  });

  it("selects a node using a keyboard-accessible control", () => {
    const select = vi.fn();
    render(<KnowledgeGraphCanvas graph={graph} onSelectNode={select} />);
    fireEvent.click(screen.getByRole("button", { name: /Policy/ }));
    expect(select).toHaveBeenCalledWith("node-1");
  });
});
