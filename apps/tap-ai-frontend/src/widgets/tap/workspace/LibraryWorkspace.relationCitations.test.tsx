import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { LibraryWorkspace } from "./LibraryWorkspace";
import { WORKSPACE_COPY } from "./copy";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});
it("loads and highlights the cited path directly from its historical snapshot", async () => {
  window.history.replaceState(
    null,
    "",
    "/?module=library&snapshot=snap_old&highlight=edge-a",
  );
  const requests: Request[] = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    requests.push(request);
    return new Response(
      JSON.stringify({
        snapshotId: "snap_old",
        isActiveSnapshot: false,
        nodes: [
          {
            nodeId: "a",
            nodeType: "CONCEPT",
            label: "Application",
            canonicalKey: "a",
          },
          {
            nodeId: "b",
            nodeType: "CONCEPT",
            label: "Disclosure",
            canonicalKey: "b",
          },
        ],
        edges: [
          {
            edgeId: "edge-a",
            sourceNodeId: "a",
            targetNodeId: "b",
            relationType: "REQUIRES",
            origin: "EXTRACTED",
            confidence: 1,
          },
        ],
      }),
      { headers: { "content-type": "application/json" } },
    );
  });
  const { container } = render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <LibraryWorkspace
        copy={WORKSPACE_COPY.en}
        graphProjectId="project-a"
        sources={[]}
      />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByText(
      "The graph has been updated. Showing the historical snapshot.",
    ),
  ).toBeVisible();
  expect(requests[0]!.url).toContain("/knowledge/graph/subgraph");
  expect(await requests[0]!.json()).toEqual({
    snapshotId: "snap_old",
    edgeIds: ["edge-a"],
  });
  expect(
    container.querySelector('.tap-graph-edge[data-highlighted="true"]'),
  ).not.toBeNull();
  expect(
    container.querySelectorAll('.tap-graph-node[data-highlighted="true"]'),
  ).toHaveLength(2);
  await userEvent.click(
    screen.getByRole("button", { name: "Back to current graph" }),
  );
  expect(window.location.search).not.toContain("snapshot=");
  expect(
    screen.queryByText(
      "The graph has been updated. Showing the historical snapshot.",
    ),
  ).not.toBeInTheDocument();
});
