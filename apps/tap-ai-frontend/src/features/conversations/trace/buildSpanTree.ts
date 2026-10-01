import type { TraceSpanView } from "../api/client";

export interface SpanNode {
  span: TraceSpanView;
  depth: number;
  children: SpanNode[];
}

function sortByStart(spans: readonly TraceSpanView[]): TraceSpanView[] {
  return [...spans].sort(
    (a, b) => Date.parse(a.startedAt) - Date.parse(b.startedAt),
  );
}

export function buildSpanTree(spans: readonly TraceSpanView[]): SpanNode[] {
  const knownIds = new Set(spans.map((span) => span.spanId));
  const childrenByParent = new Map<string | null, TraceSpanView[]>();
  for (const span of spans) {
    const parentId =
      span.parentSpanId != null && knownIds.has(span.parentSpanId)
        ? span.parentSpanId
        : null;
    const bucket = childrenByParent.get(parentId);
    if (bucket === undefined) childrenByParent.set(parentId, [span]);
    else bucket.push(span);
  }
  const build = (parentId: string | null, depth: number): SpanNode[] =>
    sortByStart(childrenByParent.get(parentId) ?? []).map((span) => ({
      span,
      depth,
      children: build(span.spanId, depth + 1),
    }));
  return build(null, 0);
}
