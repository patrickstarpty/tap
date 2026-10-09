import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  fakeKnowledgeClient,
  retrievalCitation,
} from "../testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../testing/renderKnowledgeApp";
import { relationText, type EdgeCitation } from "../model/edgeCitation";
import { EvidencePanel } from "./EvidencePanel";

function edgeCitationFixture(
  citationId: string,
  edgeId: string,
  overrides: Partial<{
    graphVersion: string;
    subject: { nodeId: string; label: string };
    object: { nodeId: string; label: string };
    relationType: string;
    relationLabel: string;
  }> = {},
): EdgeCitation {
  return {
    ...retrievalCitation(citationId),
    kind: "edge",
    edge: {
      edgeId,
      graphVersion: overrides.graphVersion ?? "2",
      subject: overrides.subject ?? {
        nodeId: "node-underwriting",
        label: "Underwriting review",
      },
      object: overrides.object ?? {
        nodeId: "node-disclosure",
        label: "Health disclosure",
      },
      relationType: overrides.relationType ?? "REQUIRES",
      relationLabel: overrides.relationLabel ?? "requires",
    },
  } as EdgeCitation;
}

describe("EvidencePanel", () => {
  it("draws every edge citation of the turn and lists the path as text", () => {
    const edge1 = edgeCitationFixture("e1", "e1");
    const edge2 = edgeCitationFixture("e2", "e2", {
      subject: { nodeId: "node-disclosure", label: "Health disclosure" },
      object: { nodeId: "node-approval", label: "Policy approval" },
      relationType: "PRECEDES",
      relationLabel: "precedes",
    });

    renderKnowledgeApp(
      <EvidencePanel
        active={{ citation: edge2, id: "e2" }}
        turnEdgeCitations={[edge1, edge2]}
        locale="en"
        onClose={() => undefined}
        onViewInLibrary={() => undefined}
      />,
      { api: fakeKnowledgeClient() },
    );

    const graph = screen.getByRole("img");
    expect(graph.getAttribute("aria-label")).toContain(relationText(edge1));
    expect(graph.getAttribute("aria-label")).toContain(relationText(edge2));

    const pathList = screen.getByRole("list", { name: "Path as text" });
    expect(pathList.querySelectorAll("li")).toHaveLength(2);
    expect(pathList).toHaveTextContent(relationText(edge1));
    expect(pathList).toHaveTextContent(relationText(edge2));

    expect(graph.querySelector('line[data-active="true"]')).not.toBeNull();
  });

  it("hands the turn's edge ids to the library", async () => {
    const edge1 = edgeCitationFixture("e1", "e1");
    const edge2 = edgeCitationFixture("e2", "e2", {
      subject: { nodeId: "node-disclosure", label: "Health disclosure" },
      object: { nodeId: "node-approval", label: "Policy approval" },
      relationType: "PRECEDES",
      relationLabel: "precedes",
    });
    const onViewInLibrary = vi.fn();

    renderKnowledgeApp(
      <EvidencePanel
        active={{ citation: edge1, id: "e1" }}
        turnEdgeCitations={[edge1, edge2]}
        locale="en"
        onClose={() => undefined}
        onViewInLibrary={onViewInLibrary}
      />,
      { api: fakeKnowledgeClient() },
    );

    await userEvent.click(
      screen.getByRole("button", { name: "View in Library" }),
    );

    expect(onViewInLibrary).toHaveBeenCalledWith(["e1", "e2"], "2");
  });
});
