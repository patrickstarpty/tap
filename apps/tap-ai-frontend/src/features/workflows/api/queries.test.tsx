import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { createTestQueryClient } from "../../../shared/testing/renderApp";
import { useWorkflowLogs, useWorkflowNode, useWorkflowTask } from "./queries";

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
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("continues task updates with no sidebar mounted", async () => {
  let snapshotReads = 0;
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
      return Response.json(
        ++snapshotReads === 1
          ? snapshot
          : {
              ...snapshot,
              status: "paused",
              lastSequence: 2,
              availableActions: ["resume", "cancel"],
            },
      );
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
  expect(result.current.snapshot?.availableActions).toContain("resume");
});

it("refreshes selected node and log data after a node event without changing the stream cursor", async () => {
  let nodeReads = 0;
  let logReads = 0;
  let snapshotReads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (request) => {
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
    if (url.endsWith("/nodes/node-1"))
      return Response.json({
        nodeId: "node-1",
        status: ++nodeReads === 1 ? "running" : "succeeded",
        attempts: [],
        artifacts: [],
      });
    if (url.includes("/nodes/node-1/logs"))
      return Response.json({
        items:
          ++logReads === 1
            ? []
            : [
                {
                  eventId: "log-2",
                  occurredAt: "2026-11-06T00:01:00Z",
                  kind: "node.finished",
                  payload: {},
                  sequence: 2,
                },
              ],
        nextCursor: null,
      });
    return Response.json({
      ...snapshot,
      nodes: [
        { nodeId: "node-1", status: "succeeded", attempt: 1, attempts: [] },
      ],
      lastSequence: ++snapshotReads === 1 ? 1 : 2,
    });
  });
  const cache = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={cache}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(
    () => ({
      task: useWorkflowTask("project-1", "task-1"),
      node: useWorkflowNode("project-1", "task-1", "node-1"),
      logs: useWorkflowLogs("project-1", "task-1", "node-1", 1),
    }),
    { wrapper },
  );
  await waitFor(() => expect(result.current.node.data?.status).toBe("running"));
  await waitFor(() =>
    expect(result.current.logs.data?.pages[0]?.items).toEqual([]),
  );
  act(() =>
    controller.enqueue(
      new TextEncoder().encode(
        'id: 2\nevent: node.finished\ndata: {"eventId":"e-2","taskId":"task-1","projectId":"project-1","sequence":2,"nodeId":"node-1","attempt":1,"kind":"node.finished","status":"succeeded","payload":{},"occurredAt":"2026-11-06T00:01:00Z"}\n\n',
      ),
    ),
  );
  await waitFor(() =>
    expect(result.current.node.data?.status).toBe("succeeded"),
  );
  await waitFor(() =>
    expect(result.current.logs.data?.pages[0]?.items).toHaveLength(1),
  );
  expect(result.current.task.snapshot?.lastSequence).toBe(2);
  expect(nodeReads).toBeGreaterThan(1);
  expect(logReads).toBeGreaterThan(1);
});

it("exposes exhausted connection failure and retries from the saved cursor", async () => {
  vi.useFakeTimers();
  let online = false;
  const cursors: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (request) => {
    const req = request as Request;
    if (req.url.includes("/events?")) {
      cursors.push(req.headers.get("Last-Event-ID") ?? "");
      if (cursors.length === 1)
        return new Response(
          'id: 2\nevent: activity\ndata: {"eventId":"e-2","taskId":"task-1","projectId":"project-1","sequence":2,"nodeId":null,"attempt":0,"kind":"activity","status":null,"payload":{},"occurredAt":"2026-11-06T00:01:00Z"}\n\n',
          { headers: { "content-type": "text/event-stream" } },
        );
      if (!online) return new Response(null, { status: 503 });
      return new Response(
        new ReadableStream<Uint8Array>({
          start(value) {
            controller = value;
          },
        }),
        { headers: { "content-type": "text/event-stream" } },
      );
    }
    return Response.json(snapshot);
  });
  const cache = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={cache}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useWorkflowTask("project-1", "task-1"), {
    wrapper,
  });
  await act(async () => {
    await vi.runAllTimersAsync();
  });
  expect(result.current.connection).toBe("error");
  expect(cursors.length).toBe(6);
  online = true;
  act(() => result.current.retry());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(cursors.at(-1)).toBe("2");
  act(() =>
    controller.enqueue(
      new TextEncoder().encode(
        'id: 3\nevent: activity\ndata: {"eventId":"e-3","taskId":"task-1","projectId":"project-1","sequence":3,"nodeId":null,"attempt":0,"kind":"activity","status":null,"payload":{},"occurredAt":"2026-11-06T00:02:00Z"}\n\n',
      ),
    ),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.connection).toBe("live");
  expect(result.current.snapshot?.lastSequence).toBe(3);
});
