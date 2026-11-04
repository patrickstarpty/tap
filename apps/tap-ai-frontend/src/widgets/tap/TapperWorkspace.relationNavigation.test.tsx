import { act, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { renderKnowledgeApp } from "../../features/knowledge/testing/renderKnowledgeApp";
import { fakeKnowledgeClient } from "../../features/knowledge/testing/fakeKnowledgeClient";
import { TapperWorkspace } from "./TapperWorkspace";

afterEach(() => window.history.replaceState(null, "", "/"));
it("restores Tapper when browser history leaves a relation graph", async () => {
  window.history.replaceState(
    null,
    "",
    "/?module=library&snapshot=old&highlight=edge",
  );
  renderKnowledgeApp(<TapperWorkspace />, { api: fakeKnowledgeClient() });
  expect(await screen.findByRole("heading", { name: "Library" })).toBeVisible();
  act(() => {
    window.history.replaceState(null, "", "/");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  expect(
    screen.queryByRole("heading", { name: "Library" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Message Tapper" })).toBeVisible();
});

it("restores the cited snapshot when history changes within Library", async () => {
  const { vi } = await import("vitest");
  vi.stubGlobal("fetch", async (input: Request | string) => {
    if (input instanceof Request && input.url.endsWith("/graph/subgraph")) {
      const { snapshotId } = (await input.json()) as { snapshotId: string };
      return Response.json({
        snapshotId,
        nodes: [
          {
            nodeId: snapshotId,
            nodeType: "CONCEPT",
            label: `Graph ${snapshotId}`,
            canonicalKey: snapshotId,
          },
        ],
        edges: [],
      });
    }
    return Response.json({ items: [] });
  });
  const url = (id: string) => `/?module=library&snapshot=${id}&highlight=edge`;
  window.history.replaceState(null, "", url("A"));
  try {
    renderKnowledgeApp(<TapperWorkspace />, { api: fakeKnowledgeClient() });
    expect((await screen.findAllByText("Graph A"))[0]).toBeVisible();
    act(() => {
      window.history.replaceState(null, "", url("B"));
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect((await screen.findAllByText("Graph B"))[0]).toBeVisible();
    act(() => {
      window.history.replaceState(null, "", url("A"));
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect((await screen.findAllByText("Graph A"))[0]).toBeVisible();
    expect(screen.queryAllByText("Graph B")).toHaveLength(0);
  } finally {
    vi.unstubAllGlobals();
  }
});
