import { expect, it, vi } from "vitest";
import { createBrowserControlTransport } from "./browserControlTransport";

it("keeps the panel capability out of URLs and sends only the confirmed epoch", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(
      async () => new Response(JSON.stringify({}), { status: 200 }),
    );
  const transport = createBrowserControlTransport(
    "project a",
    "task-a",
    "session-a",
    fetcher,
  );
  const signal = new AbortController().signal;
  await transport.issue(signal);
  await transport.state("private-token", signal);
  await transport.control(
    "private-token",
    { operation: "takeover", expectedEpoch: 4 },
    "command-one",
    signal,
  );
  const requests = fetcher.mock.calls.map(
    (call) => call[0] as unknown as Request,
  );
  expect(requests[0].url).toContain(
    "/projects/project%20a/workflows/task-a/browser-control/session-a/panel",
  );
  expect(requests[0].method).toBe("POST");
  expect(requests[1].headers.get("X-Browser-Capability")).toBe("private-token");
  expect(
    requests.every((request) => !request.url.includes("private-token")),
  ).toBe(true);
  expect(requests[2].headers.get("Idempotency-Key")).toBe("command-one");
  expect(await requests[2].json()).toEqual({
    operation: "takeover",
    expectedEpoch: 4,
  });
});

it("maps bounded frame bytes and treats 409 as waiting for a fresh frame", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          frame_sequence: 7,
          captured_at: "2026-11-10T12:00:00Z",
          control_epoch: 2,
          viewport: { width: 800, height: 600 },
          image_base64: "AA==",
          media_type: "image/jpeg",
        }),
      ),
    )
    .mockResolvedValueOnce(new Response("", { status: 409 }));
  const transport = createBrowserControlTransport(
    "project-a",
    "task-a",
    "session-a",
    fetcher,
  );
  const signal = new AbortController().signal;
  expect(transport.frame).toBeDefined();
  expect(await transport.frame!("private-token", signal)).toEqual({
    frameSequence: 7,
    capturedAt: "2026-11-10T12:00:00Z",
    controlEpoch: 2,
    viewport: { width: 800, height: 600 },
    imageDataUrl: "data:image/jpeg;base64,AA==",
  });
  expect(await transport.frame!("private-token", signal)).toBeNull();
});

it("sends human input only in the authorized action body with frame and epoch", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(new Response(JSON.stringify({ status: "completed" })));
  const transport = createBrowserControlTransport(
    "project-a",
    "task-a",
    "session-a",
    fetcher,
  );
  const command = {
    expectedEpoch: 2,
    action: {
      type: "fill" as const,
      frame_sequence: 7,
      viewport: { width: 800, height: 600 },
      x: 100,
      y: 120,
      value: "synthetic",
    },
  };
  const result = await transport.humanAction!(
    "private-token",
    command,
    "human-one",
    new AbortController().signal,
  );
  const request = fetcher.mock.calls[0][0] as Request;
  expect(request.url).toMatch(/\/actions$/u);
  expect(request.headers.get("X-Browser-Capability")).toBe("private-token");
  expect(request.headers.get("Idempotency-Key")).toBe("human-one");
  expect(await request.json()).toEqual(command);
  expect(result.status).toBe("completed");
});
