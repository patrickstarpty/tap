import { describe, expect, it } from "vitest";

import { buildSpanTree } from "./buildSpanTree";
import type { TraceSpanView } from "../api/client";

const span = (overrides: Partial<TraceSpanView>): TraceSpanView => ({
  spanId: "span-1",
  parentSpanId: null,
  name: "turn.execute",
  status: "ok",
  startedAt: "2026-09-30T00:00:00.000Z",
  durationMs: 100,
  attributes: {},
  attempt: 1,
  ...overrides,
});

describe("buildSpanTree", () => {
  it("nests children under parents with depth", () => {
    const spans = [
      span({ spanId: "root", parentSpanId: null }),
      span({
        spanId: "child",
        parentSpanId: "root",
        startedAt: "2026-09-30T00:00:01.000Z",
      }),
      span({
        spanId: "grandchild",
        parentSpanId: "child",
        startedAt: "2026-09-30T00:00:02.000Z",
      }),
    ];

    const tree = buildSpanTree(spans);

    expect(tree).toHaveLength(1);
    expect(tree[0]!.span.spanId).toBe("root");
    expect(tree[0]!.depth).toBe(0);
    expect(tree[0]!.children[0]!.span.spanId).toBe("child");
    expect(tree[0]!.children[0]!.depth).toBe(1);
    expect(tree[0]!.children[0]!.children[0]!.span.spanId).toBe("grandchild");
    expect(tree[0]!.children[0]!.children[0]!.depth).toBe(2);
  });

  it("keeps orphan spans as roots", () => {
    const spans = [
      span({ spanId: "a", parentSpanId: "missing-parent" }),
      span({ spanId: "b", parentSpanId: null }),
    ];

    const tree = buildSpanTree(spans);

    expect(tree.map((node) => node.span.spanId).sort()).toEqual(["a", "b"]);
    expect(tree.every((node) => node.depth === 0)).toBe(true);
  });

  it("sorts siblings by start time", () => {
    const spans = [
      span({
        spanId: "second",
        parentSpanId: null,
        startedAt: "2026-09-30T00:00:05.000Z",
      }),
      span({
        spanId: "first",
        parentSpanId: null,
        startedAt: "2026-09-30T00:00:01.000Z",
      }),
    ];

    const tree = buildSpanTree(spans);

    expect(tree.map((node) => node.span.spanId)).toEqual(["first", "second"]);
  });
});
