import { describe, expect, it, vi } from "vitest";

import { ConversationClientError, createConversationClient } from "./client";

describe("ConversationClient", () => {
  it("creates the first turn and appends later turns with stable idempotency keys", async () => {
    const fetcher = vi.fn(async (request: Request) => {
      void request;
      return new Response(
        JSON.stringify({
          conversationId: "conversation-1",
          turnId: "turn-1",
          state: "queued",
        }),
        { status: 202, headers: { "content-type": "application/json" } },
      );
    });
    const client = createConversationClient({
      projectId: "project-1",
      fetch: fetcher,
    });
    const input = {
      message: "What applies?",
      modelAlias: "tapper-chat",
      sourceRevisionIds: ["revision-1"],
      documentRevisionIds: [],
      agentRevisionId: "agent-r1",
      skillRevisionIds: ["skill-r1"],
    };

    await client.create(input, "request-first");
    await client.append("conversation-1", input, "request-second");

    const first = fetcher.mock.calls[0]?.[0] as Request;
    const second = fetcher.mock.calls[1]?.[0] as Request;
    expect(first.url).toContain("/projects/project-1/conversations");
    expect(first.headers.get("idempotency-key")).toBe("request-first");
    expect(second.url).toContain("/conversations/conversation-1/turns");
    expect(second.headers.get("idempotency-key")).toBe("request-second");
    expect(await second.json()).toEqual(input);
  });

  it("resumes SSE with Last-Event-ID and parses fragmented event frames", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'id: 8\nevent: answer.delta\ndata: {"eventId":"event-8",',
          ),
        );
        controller.enqueue(
          encoder.encode(
            '"sequence":8,"chatId":"conversation-1","turnId":"turn-1","occurredAt":"2026-09-09T00:00:00Z","schemaVersion":1,"event":{"type":"answer.delta","payload":{"text":"answer"}}}\n\n',
          ),
        );
        controller.close();
      },
    });
    const fetcher = vi.fn(async (request: Request) => {
      void request;
      return new Response(stream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    });
    const client = createConversationClient({
      projectId: "project-1",
      fetch: fetcher,
    });
    const events = [];
    for await (const event of client.stream("conversation-1", 7))
      events.push(event);

    const request = fetcher.mock.calls[0]?.[0] as Request;
    expect(request.headers.get("Last-Event-ID")).toBe("7");
    expect(events).toHaveLength(1);
    expect(events[0]?.sequence).toBe(8);
  });

  it("does not lose a terminal frame when CRLF is split across chunks", async () => {
    const encoder = new TextEncoder();
    const event = (sequence: number, type: string) =>
      `data: ${JSON.stringify({
        eventId: `event-${sequence}`,
        sequence,
        chatId: "conversation-1",
        turnId: "turn-1",
        occurredAt: "2026-09-09T00:00:00Z",
        schemaVersion: 1,
        event: { type, payload: {} },
      })}`;
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(`${event(1, "citation.resolved")}\r`),
        );
        controller.enqueue(
          encoder.encode(`\n\r\n${event(2, "turn.completed")}\r\n\r\n`),
        );
        controller.close();
      },
    });
    const client = createConversationClient({
      projectId: "project-1",
      fetch: async () => new Response(stream),
    });
    const events = [];
    for await (const item of client.stream("conversation-1", 0))
      events.push(item);
    expect(events.map((item) => item.event.type)).toEqual([
      "citation.resolved",
      "turn.completed",
    ]);
  });

  it("aborts a stream and exposes safe HTTP failure status", async () => {
    const fetcher = vi.fn(async (request: Request) => {
      void request;
      return new Response(JSON.stringify({ title: "Forbidden" }), {
        status: 403,
        headers: { "content-type": "application/problem+json" },
      });
    });
    const client = createConversationClient({
      projectId: "project-1",
      fetch: fetcher,
    });
    await expect(client.get("hidden")).rejects.toMatchObject({ status: 403 });
  });

  it("loads citation evidence only through its immutable Conversation Turn link", async () => {
    const fetcher = vi.fn(async (request: Request) => {
      void request;
      return new Response(JSON.stringify({ citationId: "citation-1" }));
    });
    const client = createConversationClient({
      projectId: "project-1",
      fetch: fetcher,
    });

    await client.citation("conversation-1", "turn-1", "citation-1");

    const request = fetcher.mock.calls[0]?.[0] as Request;
    expect(request.url).toContain(
      "/conversations/conversation-1/turns/turn-1/citations/citation-1",
    );
  });

  it("reports a stale historical citation using an allowlisted problem code", async () => {
    const client = createConversationClient({
      projectId: "project-1",
      fetch: async () =>
        new Response(
          JSON.stringify({
            type: "https://tap.example/problems/citation-stale",
            retryable: false,
          }),
          {
            status: 404,
            headers: { "content-type": "application/problem+json" },
          },
        ),
    });
    const error = await client
      .citation("conversation-1", "turn-1", "citation-1")
      .catch((value: unknown) => value);
    expect(error).toBeInstanceOf(ConversationClientError);
    expect(error).toMatchObject({ status: 404, code: "citation-stale" });
  });
});
