import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";

import { createTestQueryClient } from "../../../shared/testing/renderApp";
import type { ConversationTurnSummary } from "../api/client";
import { conversationKeys } from "../api/queries";
import { useConversationRuntime } from "./useConversationRuntime";

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
const input = {
  kind: "chat" as const,
  input: { message: "Question", modelAlias: "model-1" },
};
const turn = (
  state: ConversationTurnSummary["state"] = "running",
): ConversationTurnSummary => ({
  turnId: "turn-1",
  state,
  attempt: 1,
  inputSnapshotDigest: "digest",
  input: {
    message: "Question",
    modelAlias: "model-1",
    documentRevisionIds: [],
    sourceRevisionIds: [],
    resolvedResources: [],
    skillRevisionIds: [],
    skillLabels: [],
  },
});
const detail = (
  id = "conversation-1",
  state: ConversationTurnSummary["state"] = "running",
) => ({
  conversationId: id,
  title: "Question",
  createdAt: "2026-11-06T00:00:00Z",
  updatedAt: "2026-11-06T00:00:00Z",
  turns: [turn(state)],
});
const event = (
  sequence: number,
  eventType: string,
  payload: Record<string, unknown>,
) => ({
  eventId: `e-${sequence}`,
  sequence,
  turnId: "turn-1",
  occurredAt: "2026-11-06T00:00:00Z",
  eventType,
  payload,
});
function setup(id = "draft") {
  const cache = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={cache}>{children}</QueryClientProvider>
  );
  return {
    ...renderHook(() => useConversationRuntime("project-1", id), { wrapper }),
    cache,
  };
}
function transport() {
  let streamController: ReadableStreamDefaultController<Uint8Array> | undefined;
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (request) => {
      const req = request as Request;
      if (req.url.includes("/stream"))
        return new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              streamController = controller;
            },
          }),
          { headers: { "content-type": "text/event-stream" } },
        );
      if (req.url.endsWith("/events"))
        return json({ items: [], nextCursor: null });
      return json(detail(new URL(req.url).pathname.split("/").at(-1)));
    });
  return {
    fetcher,
    emit(sequence: number, type: string, payload: Record<string, unknown>) {
      streamController!.enqueue(
        new TextEncoder().encode(
          `data: ${JSON.stringify({ ...event(sequence, type, payload), chatId: "conversation-1", schemaVersion: 1, event: { type, payload } })}\n\n`,
        ),
      );
    },
    ready: () => streamController !== undefined,
  };
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  window.sessionStorage.clear();
});

it("keeps a replayed draft and labels when durable history advances beyond the live cursor", async () => {
  const wire = transport();
  const { result, cache } = setup("conversation-1");
  await waitFor(() => expect(wire.ready()).toBe(true));
  act(() =>
    wire.emit(1, "answer.draft", {
      attempt: 1,
      seq: 1,
      text: "Replayed draft [S1]",
    }),
  );
  await waitFor(() =>
    expect(result.current.turns[0]?.draft?.text).toBe("Replayed draft [S1]"),
  );
  act(() =>
    cache.setQueryData(conversationKeys.events("project-1", "conversation-1"), {
      items: [
        event(8, "retrieval.hits_ready", {
          labels: [{ label: "S1", title: "Source", location: "page 2" }],
        }),
        event(9, "stage.started", { stage: "verify" }),
      ],
      nextCursor: null,
    }),
  );
  await waitFor(() => expect(result.current.turns[0]?.stage).toBe("verify"));
  expect(result.current.turns[0]?.draft?.text).toBe("Replayed draft [S1]");
  expect(result.current.turns[0]?.citationLabels.S1?.title).toBe("Source");
});

it("ignores a late accepted send after selecting another conversation", async () => {
  const wire = transport();
  let finish!: (response: Response) => void;
  wire.fetcher.mockImplementationOnce(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  const { result } = setup();
  let sending!: ReturnType<typeof result.current.send>;
  act(() => {
    sending = result.current.send(input);
  });
  await waitFor(() => expect(finish).toBeDefined());
  act(() => result.current.selectConversation("conversation-2"));
  await act(async () => {
    finish(
      json(
        { conversationId: "conversation-1", turnId: "turn-1", state: "queued" },
        202,
      ),
    );
    expect(await sending).toBeNull();
  });
  expect(result.current.conversationId).toBe("conversation-2");
  expect(result.current.sending).toBe(false);
});

it("reserves preparation, rejects duplicate sends, and reuses the unconfirmed intent key on retry", async () => {
  const requests: Request[] = [];
  let finish!: (response: Response) => void;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (request) => {
    requests.push(request as Request);
    return new Promise<Response>((resolve) => {
      finish = resolve;
    });
  });
  const { result } = setup();
  let prepare!: (value: typeof input) => void;
  let sending!: ReturnType<typeof result.current.send>;
  act(() => {
    sending = result.current.send(
      () =>
        new Promise((resolve) => {
          prepare = resolve;
        }),
    );
  });
  await act(async () => {
    expect(await result.current.send(input)).toBeNull();
  });
  expect(result.current.sending).toBe(true);
  expect(requests).toHaveLength(0);
  await act(async () => {
    prepare(input);
  });
  const failure = expect(sending).rejects.toMatchObject({ status: 503 });
  await act(async () => {
    finish(json({ title: "Unavailable" }, 503));
    await failure;
  });
  act(() => {
    sending = result.current.send(input);
  });
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0]!.headers.get("Idempotency-Key")).toBeTruthy();
  expect(requests[1]!.headers.get("Idempotency-Key")).toBe(
    requests[0]!.headers.get("Idempotency-Key"),
  );
  const retryFailure = expect(sending).rejects.toMatchObject({ status: 503 });
  await act(async () => {
    finish(json({ title: "Unavailable" }, 503));
    await retryFailure;
  });
});

it.each(["turn.canceled", "turn.failed", "turn.completed"])(
  "stops active polling after %s despite stale running detail",
  async (type) => {
    const wire = transport();
    const { result } = setup("conversation-1");
    await waitFor(() => expect(wire.ready()).toBe(true));
    act(() =>
      wire.emit(
        2,
        type,
        type === "turn.completed"
          ? { answer: { answer: "done", citations: [] } }
          : {},
      ),
    );
    await waitFor(() => expect(result.current.hasActiveTurn).toBe(false));
    const count = wire.fetcher.mock.calls.filter(([request]) =>
      (request as Request).url.endsWith("/events"),
    ).length;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 1150));
    });
    expect(
      wire.fetcher.mock.calls.filter(([request]) =>
        (request as Request).url.endsWith("/events"),
      ),
    ).toHaveLength(count);
  },
);

it("cancels the current turn and respects terminal detail over an older running stream", async () => {
  const wire = transport();
  const original = wire.fetcher.getMockImplementation()!;
  let canceled = false;
  wire.fetcher.mockImplementation(async (request, init) => {
    if ((request as Request).url.endsWith("/cancel")) {
      canceled = true;
      return json(turn("canceled"));
    }
    if (canceled && (request as Request).url.endsWith("/conversation-1"))
      return json(detail("conversation-1", "canceled"));
    return original(request, init);
  });
  const { result } = setup("conversation-1");
  await waitFor(() => expect(wire.ready()).toBe(true));
  act(() => wire.emit(1, "turn.started", {}));
  await waitFor(() => expect(result.current.hasActiveTurn).toBe(true));
  await act(async () => {
    await result.current.cancel("turn-1");
  });
  await waitFor(() => expect(result.current.turns[0]?.status).toBe("canceled"));
  expect(result.current.hasActiveTurn).toBe(false);
});

it("retains an Insights key through a recoverable failure and remount, then clears it on acceptance", async () => {
  const requests: Request[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (request) => {
    requests.push(request as Request);
    return requests.length === 1
      ? json({ title: "Unavailable" }, 503)
      : json(
          {
            conversationId: "conversation-1",
            turnId: "turn-1",
            state: "queued",
          },
          202,
        );
  });
  const request = {
    kind: "insights" as const,
    input: {
      queryId: "query-1",
      resourceRefs: ["report-1"],
      question: "Explain",
      sourceRevisionIds: [],
      documentRevisionIds: [],
    },
  };
  const first = setup();
  await act(async () => {
    await expect(first.result.current.send(request)).rejects.toMatchObject({
      status: 503,
    });
  });
  expect(
    window.sessionStorage.getItem("tap:insights-pending:project-1"),
  ).toContain("Explain");
  first.unmount();
  const second = setup();
  await act(async () => {
    await second.result.current.send(request);
  });
  expect(requests[1]!.headers.get("Idempotency-Key")).toBe(
    requests[0]!.headers.get("Idempotency-Key"),
  );
  expect(JSON.parse(await requests[1]!.clone().text())).toEqual(request.input);
  expect(
    window.sessionStorage.getItem("tap:insights-pending:project-1"),
  ).toBeNull();
  expect(second.result.current.conversationId).toBe("conversation-1");
});

it("does not expose the prior conversation's stream facts when the next conversation needs no stream", async () => {
  const wire = transport();
  const original = wire.fetcher.getMockImplementation()!;
  wire.fetcher.mockImplementation(async (request, init) => {
    if ((request as Request).url.endsWith("/conversation-2"))
      return json(detail("conversation-2", "completed"));
    return original(request, init);
  });
  const { result } = setup("conversation-1");
  await waitFor(() => expect(wire.ready()).toBe(true));
  act(() =>
    wire.emit(1, "turn.completed", {
      answer: { answer: "Old conversation answer", citations: [] },
    }),
  );
  await waitFor(() =>
    expect(result.current.turns[0]?.response?.answer).toBe(
      "Old conversation answer",
    ),
  );
  act(() => result.current.selectConversation("conversation-2"));
  await waitFor(() =>
    expect(result.current.detail?.conversationId).toBe("conversation-2"),
  );
  expect(result.current.turns[0]?.status).toBe("completed");
  expect(result.current.turns[0]?.response).toBeNull();
  expect(result.current.sending).toBe(false);
});

it("releases a completed accepted target so a later durable turn can become active", async () => {
  const wire = transport();
  wire.fetcher.mockImplementationOnce(async () =>
    json(
      { conversationId: "conversation-1", turnId: "turn-1", state: "queued" },
      202,
    ),
  );
  const { result, cache } = setup();
  await act(async () => {
    await result.current.send(input);
  });
  await waitFor(() => expect(wire.ready()).toBe(true));
  act(() =>
    wire.emit(1, "turn.completed", {
      answer: { answer: "done", citations: [] },
    }),
  );
  await waitFor(() => expect(result.current.hasActiveTurn).toBe(false));
  act(() =>
    cache.setQueryData(conversationKeys.detail("project-1", "conversation-1"), {
      ...detail(),
      turns: [turn("completed"), { ...turn(), turnId: "turn-2" }],
    }),
  );
  await waitFor(() => expect(result.current.hasActiveTurn).toBe(true));
});

it("preserves a pending send when the current conversation is selected again", async () => {
  const wire = transport();
  const original = wire.fetcher.getMockImplementation()!;
  let finish!: (response: Response) => void;
  wire.fetcher.mockImplementation(async (request, init) => {
    if ((request as Request).method === "POST")
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    return original(request, init);
  });
  const { result } = setup("conversation-1");
  let sending!: ReturnType<typeof result.current.send>;
  act(() => {
    sending = result.current.send(input);
  });
  await waitFor(() => expect(finish).toBeDefined());
  act(() => result.current.selectConversation("conversation-1"));
  expect(result.current.sending).toBe(true);
  await act(async () => {
    finish(
      json(
        { conversationId: "conversation-1", turnId: "turn-2", state: "queued" },
        202,
      ),
    );
    expect(await sending).toMatchObject({ turnId: "turn-2" });
  });
  expect(result.current.turns.some((item) => item.turnId === "turn-2")).toBe(
    true,
  );
});

it("preserves an accepted target and streamed updates on same-conversation selection before detail catches up", async () => {
  const wire = transport();
  const original = wire.fetcher.getMockImplementation()!;
  wire.fetcher.mockImplementation(async (request, init) => {
    if ((request as Request).method === "POST")
      return json(
        { conversationId: "conversation-1", turnId: "turn-1", state: "queued" },
        202,
      );
    if ((request as Request).url.endsWith("/conversation-1"))
      return json({ ...detail(), turns: [] });
    return original(request, init);
  });
  const { result } = setup("conversation-1");
  await waitFor(() => expect(result.current.detail?.turns).toEqual([]));
  await act(async () => {
    await result.current.send(input);
  });
  await waitFor(() => expect(wire.ready()).toBe(true));
  act(() => result.current.selectConversation("conversation-1"));
  expect(result.current.hasActiveTurn).toBe(true);
  act(() =>
    wire.emit(1, "answer.draft", {
      attempt: 1,
      seq: 1,
      text: "Still generating",
    }),
  );
  await waitFor(() =>
    expect(result.current.turns[0]?.draft?.text).toBe("Still generating"),
  );
});

it("explicitly resetting an already selected draft invalidates its pending send", async () => {
  let finish!: (response: Response) => void;
  vi.spyOn(globalThis, "fetch").mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  const { result } = setup();
  let sending!: ReturnType<typeof result.current.send>;
  act(() => {
    sending = result.current.send(input);
  });
  await waitFor(() => expect(finish).toBeDefined());
  act(() => result.current.reset());
  expect(result.current.sending).toBe(false);
  await act(async () => {
    finish(
      json(
        { conversationId: "conversation-1", turnId: "turn-1", state: "queued" },
        202,
      ),
    );
    expect(await sending).toBeNull();
  });
  expect(result.current.conversationId).toBe("draft");
  expect(result.current.turns).toEqual([]);
});

it("starts a fresh chat intent after resetting a draft with an unconfirmed send", async () => {
  const wire = transport();
  const original = wire.fetcher.getMockImplementation()!;
  const writes: { request: Request; finish: (response: Response) => void }[] =
    [];
  wire.fetcher.mockImplementation(async (request, init) => {
    if ((request as Request).method === "POST") {
      return new Promise<Response>((finish) => {
        writes.push({ request: request as Request, finish });
      });
    }
    return original(request, init);
  });
  const { result } = setup();
  let first!: ReturnType<typeof result.current.send>;
  let second!: ReturnType<typeof result.current.send>;
  act(() => {
    first = result.current.send(input);
  });
  await waitFor(() => expect(writes).toHaveLength(1));
  act(() => result.current.reset());
  act(() => {
    second = result.current.send(input);
  });
  await waitFor(() => expect(writes).toHaveLength(2));
  const firstKey = writes[0]!.request.headers.get("Idempotency-Key");
  const secondKey = writes[1]!.request.headers.get("Idempotency-Key");
  expect(firstKey).toBeTruthy();
  expect(secondKey).toBeTruthy();
  expect(secondKey).not.toBe(firstKey);
  await act(async () => {
    writes[0]!.finish(
      json(
        {
          conversationId: "conversation-old",
          turnId: "turn-old",
          state: "queued",
        },
        202,
      ),
    );
    expect(await first).toBeNull();
  });
  expect(result.current.conversationId).toBe("draft");
  expect(result.current.sending).toBe(true);
  await act(async () => {
    writes[1]!.finish(
      json(
        {
          conversationId: "conversation-new",
          turnId: "turn-new",
          state: "queued",
        },
        202,
      ),
    );
    expect(await second).toMatchObject({
      conversationId: "conversation-new",
      turnId: "turn-new",
    });
  });
  expect(result.current.conversationId).toBe("conversation-new");
  expect(result.current.sending).toBe(false);
});
