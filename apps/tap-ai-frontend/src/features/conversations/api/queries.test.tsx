import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "../../../shared/testing/renderApp";
import { useConversationStream, useTurnTrace } from "./queries";

const event = (
  sequence: number,
  turnId: string,
  type: string,
  payload: Record<string, unknown>,
) =>
  JSON.stringify({
    eventId: `event-${sequence}`,
    sequence,
    chatId: "conversation-1",
    turnId,
    occurredAt: "2026-09-09T00:00:00Z",
    schemaVersion: 1,
    event: { type, payload },
  });

const stream = (...events: string[]) =>
  new Response(events.map((item) => `data: ${item}\n\n`).join(""), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });

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
      useConversationStream("project-1", "conversation-1", "turn-1", 0),
    );

    await waitFor(() => expect(result.current.error?.status).toBe(403));
    expect(fetcher).toHaveBeenCalledTimes(1);

    act(() => result.current.retry());
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });

  it("stops reconnecting after a terminal event", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      stream(
        event(1, "turn-1", "turn.completed", {
          answer: { answer: "done", citations: [] },
        }),
      ),
    );

    const { result } = renderHook(() =>
      useConversationStream("project-1", "conversation-1", "turn-1", 0),
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
      useConversationStream("project-1", "conversation-1", "turn-1", 0),
    );

    await act(async () => vi.advanceTimersByTimeAsync(4_000));
    expect(result.current.error?.retryable).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(5);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("continues an appended turn from the recovered cursor without replaying the prior terminal turn", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      stream(
        event(4, "turn-2", "turn.started", { state: "running" }),
        event(5, "turn-2", "answer.delta", { text: "second" }),
        event(6, "turn-2", "turn.completed", {
          answer: { answer: "second answer", citations: [] },
        }),
        event(7, "turn-2", "conversation.turn.completed", {
          outcome: "completed",
        }),
      ),
    );

    const { result } = renderHook(() =>
      useConversationStream("project-1", "conversation-1", "turn-2", 3),
    );

    await waitFor(() =>
      expect(result.current.state.turns["turn-2"]?.status).toBe("completed"),
    );
    expect(result.current.state.lastSequence).toBe(7);
    const request = fetcher.mock.calls[0]![0] as Request;
    expect(request.headers.get("Last-Event-ID")).toBe("3");
  });

  it("consumes a full replay until the target turn terminal instead of stopping at an older turn", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      stream(
        event(1, "turn-1", "answer.delta", { text: "first" }),
        event(2, "turn-1", "turn.completed", {
          answer: { answer: "first answer", citations: [] },
        }),
        event(3, "turn-1", "conversation.turn.completed", {
          outcome: "completed",
        }),
        event(4, "turn-2", "answer.delta", { text: "second" }),
        event(5, "turn-2", "turn.completed", {
          answer: { answer: "second answer", citations: [] },
        }),
        event(6, "turn-2", "conversation.turn.completed", {
          outcome: "completed",
        }),
      ),
    );

    const { result } = renderHook(() =>
      useConversationStream("project-1", "conversation-1", "turn-2", 0),
    );

    await waitFor(() => expect(result.current.state.lastSequence).toBe(6));
    expect(result.current.state.turns["turn-1"]?.answer).toBe("first answer");
    expect(result.current.state.turns["turn-2"]?.answer).toBe("second answer");
  });

  it("resumes a new turn in the same conversation from the consumed stream cursor", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        stream(
          event(5, "turn-1", "turn.completed", {
            answer: { answer: "first answer", citations: [] },
          }),
          event(6, "turn-1", "conversation.turn.completed", {
            outcome: "completed",
          }),
        ),
      )
      .mockResolvedValueOnce(
        stream(
          event(7, "turn-2", "turn.completed", {
            answer: { answer: "second answer", citations: [] },
          }),
        ),
      );

    const { result, rerender } = renderHook(
      ({ turnId }) =>
        useConversationStream("project-1", "conversation-1", turnId, 1),
      { initialProps: { turnId: "turn-1" } },
    );
    await waitFor(() => expect(result.current.state.lastSequence).toBe(6));
    rerender({ turnId: "turn-2" });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));

    const resumed = fetcher.mock.calls[1]![0] as Request;
    expect(resumed.headers.get("Last-Event-ID")).toBe("6");
  });

  it("reconnects from the last consumed event without duplicating streamed text", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        stream(event(1, "turn-2", "answer.delta", { text: "second " })),
      )
      .mockResolvedValueOnce(
        stream(
          event(1, "turn-2", "answer.delta", { text: "second " }),
          event(2, "turn-2", "answer.delta", { text: "answer" }),
          event(3, "turn-2", "turn.completed", {
            answer: { answer: "second answer", citations: [] },
          }),
        ),
      );

    const { result, unmount } = renderHook(() =>
      useConversationStream("project-1", "conversation-1", "turn-2", 0),
    );
    await act(async () => vi.advanceTimersByTimeAsync(250));

    expect(fetcher).toHaveBeenCalledTimes(2);
    const resumed = fetcher.mock.calls[1]![0] as Request;
    expect(resumed.headers.get("Last-Event-ID")).toBe("1");
    expect(result.current.state.turns["turn-2"]?.answer).toBe("second answer");
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("useTurnTrace", () => {
  const traceJson = (hasLatestSpan: boolean) =>
    new Response(
      JSON.stringify({
        traceId: "trace-1",
        summary: {
          totalDurationMs: 100,
          inputTokens: 1,
          outputTokens: 1,
          costUsd: "0.0001",
          costIncomplete: false,
          requestedModels: ["gpt-4o"],
          upstreamModels: ["gpt-4o"],
          attemptCount: 1,
        },
        spans: hasLatestSpan
          ? [
              {
                spanId: "span-1",
                parentSpanId: null,
                name: "turn.execute",
                status: "ok",
                startedAt: "2026-09-30T00:00:00Z",
                durationMs: 10,
                attributes: {},
                attempt: 1,
              },
            ]
          : [],
        modelCalls: [],
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );

  function makeWrapper() {
    const queryClient = createTestQueryClient();
    return function wrapper({ children }: { children: ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      );
    };
  }

  it("does not fetch trace before turn is terminal", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(traceJson(true));

    renderHook(
      () =>
        useTurnTrace("project-1", "conversation-1", "turn-1", {
          enabled: false,
          latestAttempt: 1,
        }),
      { wrapper: makeWrapper() },
    );

    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("refetches trace until latest attempt execute span appears", async () => {
    vi.useFakeTimers();
    let call = 0;
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => {
        call += 1;
        return traceJson(call >= 3);
      });

    renderHook(
      () =>
        useTurnTrace("project-1", "conversation-1", "turn-1", {
          enabled: true,
          latestAttempt: 1,
        }),
      { wrapper: makeWrapper() },
    );

    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(fetcher).toHaveBeenCalledTimes(3);
    await act(async () => vi.advanceTimersByTimeAsync(4000));
    expect(fetcher).toHaveBeenCalledTimes(3);
    vi.useRealTimers();
  });

  it("stops refetching after four attempts", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => traceJson(false));

    renderHook(
      () =>
        useTurnTrace("project-1", "conversation-1", "turn-1", {
          enabled: true,
          latestAttempt: 1,
        }),
      { wrapper: makeWrapper() },
    );

    await act(async () => vi.advanceTimersByTimeAsync(0));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(fetcher).toHaveBeenCalledTimes(4);
    await act(async () => vi.advanceTimersByTimeAsync(4000));
    expect(fetcher).toHaveBeenCalledTimes(4);
    vi.useRealTimers();
  });
});
