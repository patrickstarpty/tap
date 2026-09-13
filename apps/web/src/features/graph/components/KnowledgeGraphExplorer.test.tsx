import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useActiveGraph, useGraphSearch } from "../api/queries";
import { KnowledgeGraphExplorer } from "./KnowledgeGraphExplorer";

vi.mock("../api/queries", () => ({
  useActiveGraph: vi.fn(),
  useGraphSearch: vi.fn(),
}));

const active = vi.mocked(useActiveGraph);
const search = vi.mocked(useGraphSearch);

describe("KnowledgeGraphExplorer", () => {
  beforeEach(() => {
    active.mockReturnValue({
      data: { items: [{ snapshotId: "snapshot-1" }] },
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    } as never);
    search.mockReturnValue({
      data: {
        snapshotId: "snapshot-1",
        nodes: [
          {
            nodeId: "node-1",
            label: "Claims policy",
            nodeType: "ENTITY",
            canonicalKey: "claims-policy",
            evidenceIds: ["evidence-1"],
          },
        ],
        edges: [],
        evidence: [
          {
            evidenceId: "evidence-1",
            sourceRevisionId: "revision-1",
            documentRevisionId: "revision-1",
            chunkId: "chunk-1",
            anchor: { kind: "text", start: 0, end: 5 },
            contentDigest: `sha256:${"a".repeat(64)}`,
          },
        ],
      },
      isPending: false,
      isError: false,
    } as never);
  });

  it("loads the active bounded graph and exposes grounded evidence", () => {
    render(
      <KnowledgeGraphExplorer
        projectId="tapper-demo"
        sourceRevisionIds={["revision-1"]}
      />,
    );
    expect(active).toHaveBeenCalledWith("tapper-demo", ["revision-1"]);
    expect(search).toHaveBeenCalledWith("tapper-demo", "snapshot-1", "*");
    fireEvent.click(screen.getByRole("button", { name: /Claims policy/u }));
    expect(
      screen.getByRole("link", { name: /revision-1 · chunk-1/u }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("evidence/evidence-1?snapshotId=snapshot-1"),
    );
  });

  it("shows an explicit unavailable state without rendering an empty graph", () => {
    active.mockReturnValue({
      isPending: false,
      isError: true,
      refetch: vi.fn(),
    } as never);
    search.mockReturnValue({ isPending: true, isError: false } as never);
    render(
      <KnowledgeGraphExplorer
        projectId="tapper-demo"
        sourceRevisionIds={["revision-1"]}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      /temporarily unavailable/u,
    );
  });
});
