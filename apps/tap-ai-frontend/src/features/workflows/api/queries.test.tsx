import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { createTestQueryClient } from "../../../shared/testing/renderApp";
import { useWorkflowTask } from "./queries";

const snapshot = {
  taskId: "task-1",
  projectId: "project-1",
  conversationId: "conversation-1",
  status: "running",
  planRevision: 1,
  lastSequence: 1,
  origin: "native",
  availableActions: ["steer", "pause", "cancel"],
  displayName: "Tapper",
  goalSummary: "Review login",
  createdAt: "2026-11-06T00:00:00Z",
  nodes: [],
  edges: [],
  commands: [],
  artifacts: [],
  events: [],
  budgetLimit: {},
  budgetUsed: {},
};
let controller: ReadableStreamDefaultController<Uint8Array>;
afterEach(() => vi.restoreAllMocks());

it("continues task updates with no sidebar mounted", async () => {
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (request) => {
      const url = (request as Request).url;
      if (url.includes("/events?"))
        return new Response(
          new ReadableStream<Uint8Array>({
            start(value) {
              controller = value;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      return Response.json(snapshot);
    });
  const cache = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={cache}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useWorkflowTask("project-1", "task-1"), {
    wrapper,
  });
  await waitFor(() => expect(result.current.snapshot?.lastSequence).toBe(1));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(([request]) =>
        (request as Request).url.includes("/events?"),
      ),
    ).toBe(true),
  );
  act(() =>
    controller.enqueue(
      new TextEncoder().encode(
        'id: 2\nevent: task.status\ndata: {"eventId":"e-2","taskId":"task-1","projectId":"project-1","sequence":2,"nodeId":null,"attempt":0,"kind":"task.status","status":"paused","payload":{},"occurredAt":"2026-11-06T00:01:00Z"}\n\n',
      ),
    ),
  );
  await waitFor(() => expect(result.current.snapshot?.status).toBe("paused"));
});
