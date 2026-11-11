import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { fakeKnowledgeClient } from "../../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../../features/knowledge/testing/renderKnowledgeApp";
import { createTestQueryClient } from "../../shared/testing/renderApp";
import { TapperWorkspace } from "./TapperWorkspace";
afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});
it("renders the real browser composer inline without full-app navigation or execution on mount", async () => {
  const api = fakeKnowledgeClient();
  const queryClient = createTestQueryClient();
  queryClient.setQueryData(["runtime-mode"], {
    mode: "validation",
    identityMode: "validation",
    projectId: api.projectId,
    actorId: "actor-test",
    browserEnabled: true,
    workflowEnabled: true,
    browserModelAlias: "qwen-plus",
  });
  const writes: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (request) => {
    const req = request as Request;
    if (req.method === "POST") writes.push(req.url);
    return new Response(JSON.stringify({ items: [], nextCursor: null }));
  });
  renderKnowledgeApp(<TapperWorkspace variant="lca-embedded" />, {
    api,
    queryClient,
  });
  expect(screen.queryByRole("navigation", { name: "Product" })).toBeNull();
  expect(screen.getByRole("textbox", { name: "Message Tapper" })).toBeVisible();
  expect(screen.getByLabelText("Execution")).toHaveValue("browser");
  expect(screen.getByLabelText("Execution")).toBeDisabled();
  expect(
    screen.getByRole("group", { name: "Required inputs for verification" }),
  ).toBeVisible();
  expect(writes).toEqual([]);
});

it("submits the actual reviewed browser composer payload and records its one durable receipt", async () => {
  const api = fakeKnowledgeClient();
  const queryClient = createTestQueryClient();
  const context = {
    available: true as const,
    project_id: api.projectId,
    actor_id: "actor-test",
    identity_mode: "validation" as const,
    gateway_instance_id: "gateway-1",
    context_binding: "a".repeat(64),
  };
  queryClient.setQueryData(["runtime-mode"], {
    mode: "validation",
    identityMode: "validation",
    projectId: api.projectId,
    actorId: "actor-test",
    browserEnabled: true,
    workflowEnabled: true,
    browserModelAlias: "qwen-plus",
  });
  const admitted: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (request) => {
    if (request === "/api/v1/browser-context")
      return new Response(JSON.stringify(context));
    const req = request as Request;
    if (req.method === "POST") {
      admitted.push(await req.json());
      return new Response(
        JSON.stringify({
          conversationId: "conversation-1",
          turnId: "turn-1",
          state: "queued",
        }),
        { status: 202 },
      );
    }
    if (req.url.endsWith("/conversation-1"))
      return new Response(
        JSON.stringify({
          conversationId: "conversation-1",
          title: "Read order",
          turns: [],
          createdAt: "2026-11-11T00:00:00Z",
          updatedAt: "2026-11-11T00:00:00Z",
        }),
      );
    return new Response(JSON.stringify({ items: [], nextCursor: null }));
  });
  renderKnowledgeApp(
    <TapperWorkspace variant="lca-embedded" lcaContext={context} />,
    { api, queryClient },
  );
  const user = userEvent.setup();
  await user.type(
    screen.getByRole("textbox", { name: "Message Tapper" }),
    "Read order at http://example.test/",
  );
  await user.click(screen.getByRole("button", { name: "Add required input" }));
  await user.type(screen.getByLabelText("Field ID"), "customer");
  await user.type(screen.getByLabelText("Input value", { exact: true }), "Ada");
  await user.type(screen.getByLabelText("Expected readback value"), "Ada");
  await user.type(
    screen.getByLabelText("Exact business labels (one per line)"),
    "Customer",
  );
  await user.click(
    screen.getByLabelText(
      "I reviewed every required input and its expected readback.",
    ),
  );
  await user.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(admitted).toHaveLength(1));
  expect(admitted[0]).toMatchObject({
    message: "Read order at http://example.test/",
    requestedAgentIds: [],
    browser: {
      startUrl: "http://example.test/",
      acceptanceContract: {
        reviewed: true,
        fields: [{ id: "customer", value: "Ada" }],
      },
    },
  });
  expect(
    JSON.parse(sessionStorage.getItem("tap:browser-intent:v1")!).conversationId,
  ).toBe("conversation-1");
});
