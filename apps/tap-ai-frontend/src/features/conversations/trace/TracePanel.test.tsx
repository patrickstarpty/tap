import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TracePanel } from "./TracePanel";
import type { TurnTrace } from "../api/client";

const trace: TurnTrace = {
  traceId: "trace-1",
  summary: {
    totalDurationMs: 2000,
    inputTokens: 100,
    outputTokens: 200,
    costUsd: "0.0100",
    costIncomplete: false,
    requestedModels: ["gpt-4o"],
    upstreamModels: ["gpt-4o-2024"],
    attemptCount: 2,
  },
  spans: [
    {
      spanId: "execute-1",
      parentSpanId: null,
      name: "turn.execute",
      status: "ok",
      startedAt: "2026-09-30T00:00:00.000Z",
      durationMs: 900,
      attributes: {},
      attempt: 1,
    },
    {
      spanId: "retrieval-1",
      parentSpanId: "execute-1",
      name: "retrieval.search",
      status: "ok",
      startedAt: "2026-09-30T00:00:00.100Z",
      durationMs: 200,
      attempt: 1,
      attributes: {
        "tap.retrieval.chunk_ids": ["chunk-1"],
        "tap.retrieval.document_ids": ["document-1"],
        "tap.retrieval.scores": [0.9],
        "tap.retrieval.hit_count": 1,
      },
    },
    {
      spanId: "execute-2",
      parentSpanId: null,
      name: "turn.execute",
      status: "ok",
      startedAt: "2026-09-30T00:00:01.000Z",
      durationMs: 700,
      attributes: {},
      attempt: 2,
    },
    {
      spanId: "model-call-2",
      parentSpanId: "execute-2",
      name: "chat.completion",
      status: "ok",
      startedAt: "2026-09-30T00:00:01.100Z",
      durationMs: 500,
      attempt: 2,
      attributes: { "tap.model_call_id": "call-2" },
    },
  ],
  modelCalls: [],
};

describe("TracePanel", () => {
  it("switches attempts", async () => {
    render(
      <TracePanel
        trace={trace}
        locale="en"
        onOpenModelCall={() => undefined}
        onOpenDocument={() => undefined}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Trace/u }));

    expect(screen.getByRole("tab", { name: "Attempt 2" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.queryByText("Retrieval search")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Attempt 1" }));
    expect(screen.getByText("Retrieval search")).toBeInTheDocument();
  });

  it("retrieval hit opens document", async () => {
    const onOpenDocument = vi.fn();
    render(
      <TracePanel
        trace={trace}
        locale="en"
        onOpenModelCall={() => undefined}
        onOpenDocument={onOpenDocument}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Trace/u }));
    await userEvent.click(screen.getByRole("tab", { name: "Attempt 1" }));

    await userEvent.click(screen.getByRole("link", { name: "Unknown source" }));
    expect(onOpenDocument).toHaveBeenCalledWith("document-1");
  });

  it("retrieval hit shows document name", async () => {
    render(
      <TracePanel
        trace={trace}
        locale="en"
        onOpenModelCall={() => undefined}
        onOpenDocument={() => undefined}
        documentName={(documentId) =>
          documentId === "document-1" ? "Policy.md" : undefined
        }
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Trace/u }));
    await userEvent.click(screen.getByRole("tab", { name: "Attempt 1" }));

    expect(screen.getByRole("link", { name: "Policy.md" })).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Unknown source" }),
    ).not.toBeInTheDocument();
  });

  it("model call row opens drawer", async () => {
    const onOpenModelCall = vi.fn();
    render(
      <TracePanel
        trace={trace}
        locale="en"
        onOpenModelCall={onOpenModelCall}
        onOpenDocument={() => undefined}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Trace/u }));

    await userEvent.click(screen.getByRole("button", { name: "View call" }));
    expect(onOpenModelCall).toHaveBeenCalledWith("call-2");
  });
});
