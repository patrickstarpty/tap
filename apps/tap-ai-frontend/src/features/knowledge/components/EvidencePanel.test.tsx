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

    const activeLines = graph.querySelectorAll('line[data-active="true"]');
    expect(activeLines).toHaveLength(1);
    expect(activeLines[0]).toHaveAttribute("data-edge-id", "e2");
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

  it("dedupes an edge cited twice by the same turn", async () => {
    const edge1 = edgeCitationFixture("e1", "e1");
    const edge1Again = edgeCitationFixture("e1-b", "e1");
    const onViewInLibrary = vi.fn();

    renderKnowledgeApp(
      <EvidencePanel
        active={{ citation: edge1, id: "e1" }}
        turnEdgeCitations={[edge1, edge1Again]}
        locale="en"
        onClose={() => undefined}
        onViewInLibrary={onViewInLibrary}
      />,
      { api: fakeKnowledgeClient() },
    );

    const graph = screen.getByRole("img");
    expect(graph.querySelectorAll("line")).toHaveLength(1);
    const pathList = screen.getByRole("list", { name: "Path as text" });
    expect(pathList.querySelectorAll("li")).toHaveLength(1);

    await userEvent.click(
      screen.getByRole("button", { name: "View in Library" }),
    );
    expect(onViewInLibrary).toHaveBeenCalledWith(["e1"], "2");
  });

  it("caps the edge ids handed to the library at 20, the highlight state's own limit", async () => {
    // `readGraphHighlight` (features/graph/model/highlight.ts) rejects a
    // highlight state with more than 20 edge ids -- a turn whose answer
    // cites more than that must still cap its "View in Library" request
    // rather than build a state `pushGraphHighlight` would write but the
    // reader would then immediately discard as invalid.
    const edges = Array.from({ length: 25 }, (_, index) =>
      edgeCitationFixture(`e${index}`, `e${index}`),
    );
    const onViewInLibrary = vi.fn();

    renderKnowledgeApp(
      <EvidencePanel
        active={{ citation: edges[0]!, id: "e0" }}
        turnEdgeCitations={edges}
        locale="en"
        onClose={() => undefined}
        onViewInLibrary={onViewInLibrary}
      />,
      { api: fakeKnowledgeClient() },
    );

    await userEvent.click(
      screen.getByRole("button", { name: "View in Library" }),
    );

    expect(onViewInLibrary).toHaveBeenCalledTimes(1);
    const [edgeIds] = onViewInLibrary.mock.calls[0]!;
    expect(edgeIds).toHaveLength(20);
    expect(edgeIds).toEqual(
      edges.slice(0, 20).map((edge) => edge.edge!.edgeId),
    );
  });

  it("closes on Escape and returns focus to the opening chip", async () => {
    const edge1 = edgeCitationFixture("e1", "e1");
    const chip = document.createElement("button");
    document.body.appendChild(chip);
    const onClose = vi.fn();

    renderKnowledgeApp(
      <EvidencePanel
        active={{ citation: edge1, id: "e1" }}
        turnEdgeCitations={[edge1]}
        locale="en"
        onClose={onClose}
        returnFocusTo={chip}
        onViewInLibrary={() => undefined}
      />,
      { api: fakeKnowledgeClient() },
    );

    screen.getByRole("heading", { name: "Relation evidence" }).focus();
    await userEvent.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalled();
    await vi.waitFor(() => expect(chip).toHaveFocus());
    document.body.removeChild(chip);
  });

  it("shows an error with no retry for a non-retryable snippet failure", async () => {
    const edge1 = edgeCitationFixture("e1", "e1");
    const api = fakeKnowledgeClient().withCitationProblem(new Error("boom"));

    renderKnowledgeApp(
      <EvidencePanel
        active={{ citation: edge1, id: "e1" }}
        turnEdgeCitations={[edge1]}
        locale="en"
        onClose={() => undefined}
        onViewInLibrary={() => undefined}
      />,
      { api },
    );

    expect(
      await screen.findByText("Cited content could not be checked. Try again."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Retry verification" }),
    ).not.toBeInTheDocument();
  });
});
