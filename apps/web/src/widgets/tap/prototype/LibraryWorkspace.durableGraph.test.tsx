import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { createKnowledgeClient } from "../../../features/knowledge/api/client";
import {
  useActiveGraph,
  useGraphSearch,
} from "../../../features/graph/api/queries";
import { PROTOTYPE_COPY } from "./copy";
import { LibraryWorkspace } from "./LibraryWorkspace";

vi.mock("../../../features/knowledge/api/client", () => ({
  createKnowledgeClient: vi.fn(),
}));
vi.mock("../../../features/graph/api/queries", () => ({
  useActiveGraph: vi.fn(),
  useGraphSearch: vi.fn(),
}));

it("renders a published source graph with the established prototype controls", async () => {
  const getSource = vi.fn(async (sourceId: string) => ({
    documents: { items: [{ status: "ready", revisionId: `rev_${sourceId}` }] },
  }));
  vi.mocked(createKnowledgeClient).mockReturnValue({ getSource } as never);
  vi.mocked(useActiveGraph).mockImplementation(
    (_projectId, revisionIds) =>
      ({
        data: revisionIds.length
          ? { items: [{ snapshotId: `snap_${revisionIds[0]}` }] }
          : undefined,
        isPending: revisionIds.length === 0,
        isError: false,
      }) as never,
  );
  vi.mocked(useGraphSearch).mockImplementation(
    (_projectId, snapshotId) =>
      ({
        data: snapshotId
          ? {
              snapshotId,
              nodes: [
                {
                  nodeId: "doc",
                  nodeType: "DOCUMENT",
                  label: "rev_source",
                  canonicalKey: "doc",
                },
                {
                  nodeId: "age",
                  nodeType: "CONCEPT",
                  label: "Age eligibility",
                  canonicalKey: "age",
                },
              ],
              edges: [
                {
                  edgeId: "edge",
                  sourceNodeId: "doc",
                  targetNodeId: "age",
                  relationType: "CONTAINS",
                  origin: "EXTRACTED",
                  confidence: 1,
                },
              ],
            }
          : undefined,
        isPending: snapshotId === null,
        isError: false,
      }) as never,
  );

  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <LibraryWorkspace
        copy={PROTOTYPE_COPY.en}
        locale="en"
        graphProjectId="tapper-demo"
        sources={[
          {
            id: "src_a",
            name: "Underwriting rules",
            type: "Markdown",
            status: "ready",
            origin: "knowledge-base",
            description: "Published source",
          },
          {
            id: "src_b",
            name: "Claims control",
            type: "Markdown",
            status: "ready",
            origin: "knowledge-base",
            description: "Published source",
          },
        ]}
      />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
  expect(screen.getByText(/Illustrative view/i)).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Domain overview" }),
  ).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(
    screen.getByRole("button", { name: "Published source graph" }),
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Zoom in" })).toBeVisible(),
  );
  expect(
    screen.getByRole("group", { name: "Life insurance knowledge graph" }),
  ).toBeVisible();
  expect(
    screen.getByText(/nodes and relationships come from the service/i),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: /Age eligibility/ })).toBeVisible();
  expect(vi.mocked(useActiveGraph).mock.lastCall?.[1]).toEqual(["rev_src_a"]);

  await userEvent.selectOptions(
    screen.getByRole("combobox", { name: "Graph source" }),
    "src_b",
  );
  await waitFor(() =>
    expect(getSource).toHaveBeenCalledWith("src_b", expect.anything()),
  );
  await waitFor(() =>
    expect(vi.mocked(useActiveGraph).mock.lastCall?.[1]).toEqual(["rev_src_b"]),
  );
});
