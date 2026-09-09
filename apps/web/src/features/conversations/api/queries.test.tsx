import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useConversationStream } from "./queries";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("useConversationStream", () => {
  it("does not reconnect permission failures and allows an explicit recovery", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ title: "Forbidden" }), { status: 403 }),
      );
    const { result } = renderHook(() =>
      useConversationStream("project-1", "conversation-1"),
    );

    await waitFor(() => expect(result.current.error?.status).toBe(403));
    expect(fetcher).toHaveBeenCalledTimes(1);

    act(() => result.current.retry());
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });

  it("stops reconnecting after a terminal event", async () => {
    const event = JSON.stringify({
      eventId: "event-1",
      sequence: 1,
      chatId: "conversation-1",
      turnId: "turn-1",
      occurredAt: "2026-09-09T00:00:00Z",
      schemaVersion: 1,
      event: {
        type: "turn.completed",
        payload: { answer: { answer: "done", citations: [] } },
      },
    });
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(`data: ${event}\n\n`, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const { result } = renderHook(() =>
      useConversationStream("project-1", "conversation-1"),
    );

    await waitFor(() =>
      expect(result.current.state.turns["turn-1"]?.status).toBe("completed"),
    );
    await new Promise((resolve) => window.setTimeout(resolve, 300));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("bounds retryable network reconnects and clears the backoff timer", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new TypeError("offline"));
    const { result, unmount } = renderHook(() =>
      useConversationStream("project-1", "conversation-1"),
    );

    await act(async () => vi.advanceTimersByTimeAsync(4_000));
    expect(result.current.error?.retryable).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(5);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
