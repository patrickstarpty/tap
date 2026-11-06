import { expect, it, vi } from "vitest";
import { createWorkflowClient } from "./client";

it("lists matching Agent runs with a stable ID and cursor", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(Response.json({ items: [], nextCursor: null }));
  const page = await createWorkflowClient("project-1", fetcher).list({
    agentId: "sdet",
    cursor: "task-3",
  });
  expect(page.items).toEqual([]);
  const request = fetcher.mock.calls[0]![0] as Request;
  expect(new URL(request.url).pathname).toBe(
    "/api/v1/projects/project-1/workflows",
  );
  expect(new URL(request.url).searchParams.get("agentId")).toBe("sdet");
  expect(new URL(request.url).searchParams.get("cursor")).toBe("task-3");
});

it("replays SSE after the snapshot cursor without consuming private fields", async () => {
  const fetcher = vi.fn().mockResolvedValue(
    new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              'id: 9\nevent: node.started\ndata: {"eventId":"e-9","taskId":"task-1","sequence":9,"nodeId":"node-1","attempt":1,"kind":"node.started","payload":{},"occurredAt":"2026-11-06T00:00:00Z"}\n\n',
            ),
          );
          controller.close();
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    ),
  );
  const events = [];
  for await (const item of createWorkflowClient("project-1", fetcher).stream(
    "task-1",
    8,
  ))
    events.push(item);
  expect(events.map((item) => item.sequence)).toEqual([9]);
  const request = fetcher.mock.calls[0]![0] as Request;
  expect(request.headers.get("Last-Event-ID")).toBe("8");
  expect(new URL(request.url).searchParams.get("snapshotSequence")).toBe("8");
});
