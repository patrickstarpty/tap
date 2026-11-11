import { expect, it, vi } from "vitest";
import { createRecorderClient, RecorderError } from "./recorderClient";
it("uses owner-scoped same-origin endpoints and ephemeral capability, not service authorization", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(new Response(JSON.stringify({ status: "completed" })));
  const client = createRecorderClient(fetcher);
  await client.action(
    "p",
    "flow",
    "session",
    {
      action: {
        type: "fill",
        selector: "#name",
        value: "Ada",
        frame_sequence: 5,
        viewport: { width: 800, height: 600 },
      },
      expected_epoch: 3,
    },
    "stable-command",
    "opaque",
  );
  const [path, init] = fetcher.mock.calls[0];
  expect(path).toBe(
    "/api/projects/p/lca/flows/flow/recordings/session/actions",
  );
  expect(init?.credentials).toBe("same-origin");
  expect(init?.cache).toBe("no-store");
  const headers = new Headers(init?.headers);
  expect(headers.get("X-LCA-Capability")).toBe("opaque");
  expect(headers.get("Idempotency-Key")).toBe("stable-command");
  expect(headers.has("Authorization")).toBe(false);
  expect(JSON.parse(init?.body as string).expected_epoch).toBe(3);
});
it("does not retry ambiguous recording HTTP conflicts", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(new Response("", { status: 409 }));
  await expect(
    createRecorderClient(fetcher).stop("p", "f", "s", 2, "stop-key", "opaque"),
  ).rejects.toBeInstanceOf(RecorderError);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("identifies only the exact frame-pending response on read-only frame requests", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(
      async () =>
        new Response(
          JSON.stringify({ detail: { code: "live_frame_pending" } }),
          { status: 409 },
        ),
    );
  const client = createRecorderClient(fetcher);
  await expect(client.frame("p", "f", "s", "opaque")).rejects.toMatchObject({
    status: 409,
    code: "live_frame_pending",
  });
  await expect(
    client.stop("p", "f", "s", 2, "stop-key", "opaque"),
  ).rejects.toMatchObject({
    status: 409,
    code: undefined,
  });
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it.each([
  [409, { detail: "live frame pending" }],
  [409, { detail: { code: "live_frame_pending_extra" } }],
  [403, { detail: { code: "live_frame_pending" } }],
  [409, null],
])(
  "does not treat an unrelated frame error as pending (%s, %j)",
  async (status, body) => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify(body), { status }));
    await expect(
      createRecorderClient(fetcher).frame("p", "f", "s", "opaque"),
    ).rejects.toMatchObject({
      status,
      code: undefined,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);
