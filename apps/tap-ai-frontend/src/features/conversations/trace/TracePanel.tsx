import { Button } from "antd";
import { useMemo, useState } from "react";

import { buildSpanTree, type SpanNode } from "./buildSpanTree";
import { formatTraceSummary } from "./formatTraceSummary";
import type { TraceSpanView, TurnTrace } from "../api/client";
import type { components } from "../../../shared/api/generated/schema";

type JsonValue = components["schemas"]["JsonValue"];

const SPAN_LABELS: Record<string, { en: string; zh: string }> = {
  "turn.execute": { en: "Turn execution", zh: "回合执行" },
  "chat.plan": { en: "Answer plan", zh: "回答规划" },
  "retrieval.search": { en: "Retrieval search", zh: "检索" },
  "graph.enrich": { en: "Graph enrichment", zh: "图谱扩展" },
  "citations.resolve": { en: "Citation resolution", zh: "引用解析" },
};

function spanLabel(name: string, locale: "en" | "zh"): string {
  const entry = SPAN_LABELS[name];
  if (entry === undefined) return name;
  return locale === "zh" ? entry.zh : entry.en;
}

function asString(value: JsonValue | undefined): string | null {
  return typeof value === "string" ? value : null;
}

function asStringArray(value: JsonValue | undefined): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function flatten(nodes: readonly SpanNode[]): SpanNode[] {
  return nodes.flatMap((node) => [node, ...flatten(node.children)]);
}

export function TracePanel({
  trace,
  locale,
  onOpenModelCall,
  onOpenDocument,
}: {
  trace: TurnTrace;
  locale: "en" | "zh";
  onOpenModelCall: (callId: string) => void;
  onOpenDocument: (documentId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const attempts = useMemo(() => {
    const values = new Set<number>();
    for (const span of trace.spans) {
      if (typeof span.attempt === "number") values.add(span.attempt);
    }
    return [...values].sort((a, b) => a - b);
  }, [trace.spans]);
  const [attempt, setAttempt] = useState<number | null>(
    attempts.length > 0 ? attempts[attempts.length - 1]! : null,
  );
  const selectedAttempt = attempt ?? attempts.at(-1) ?? null;

  const attemptSpans = useMemo(
    () =>
      trace.spans.filter(
        (span) => span.attempt == null || span.attempt === selectedAttempt,
      ),
    [trace.spans, selectedAttempt],
  );
  const rows = useMemo(
    () => flatten(buildSpanTree(attemptSpans)),
    [attemptSpans],
  );
  const traceStart = Math.min(
    ...attemptSpans.map((span) => Date.parse(span.startedAt)),
    Date.now(),
  );
  const traceEnd = Math.max(
    ...attemptSpans.map((span) => Date.parse(span.startedAt) + span.durationMs),
    traceStart + 1,
  );
  const totalMs = Math.max(1, traceEnd - traceStart);

  return (
    <div className="tap-trace-panel">
      <Button
        type="text"
        className="tap-trace-summary"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {locale === "zh" ? "调用链" : "Trace"}:{" "}
        {formatTraceSummary(trace.summary, locale)}
      </Button>
      {expanded ? (
        <div className="tap-trace-body">
          {attempts.length > 1 ? (
            <div role="tablist" className="tap-trace-attempts">
              {attempts.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={value === selectedAttempt}
                  onClick={() => setAttempt(value)}
                >
                  {locale === "zh" ? `第 ${value} 次尝试` : `Attempt ${value}`}
                </button>
              ))}
            </div>
          ) : null}
          <ul className="tap-trace-waterfall" role="list">
            {rows.map(({ span, depth }) => (
              <TraceRow
                key={span.spanId}
                span={span}
                depth={depth}
                locale={locale}
                traceStart={traceStart}
                totalMs={totalMs}
                onOpenModelCall={onOpenModelCall}
                onOpenDocument={onOpenDocument}
              />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function TraceRow({
  span,
  depth,
  locale,
  traceStart,
  totalMs,
  onOpenModelCall,
  onOpenDocument,
}: {
  span: TraceSpanView;
  depth: number;
  locale: "en" | "zh";
  traceStart: number;
  totalMs: number;
  onOpenModelCall: (callId: string) => void;
  onOpenDocument: (documentId: string) => void;
}) {
  const left = ((Date.parse(span.startedAt) - traceStart) / totalMs) * 100;
  const width = Math.max(2, (span.durationMs / totalMs) * 100);
  const modelCallId = asString(span.attributes["tap.model_call_id"]);
  const chunkIds = asStringArray(span.attributes["tap.retrieval.chunk_ids"]);
  const documentIds = asStringArray(
    span.attributes["tap.retrieval.document_ids"],
  );

  return (
    <li
      role="listitem"
      className={
        span.status === "error"
          ? "tap-trace-row tap-trace-row-error"
          : "tap-trace-row"
      }
    >
      <span className="tap-trace-row-name" style={{ paddingLeft: depth * 16 }}>
        {spanLabel(span.name, locale)}
      </span>
      <span className="tap-trace-row-timeline">
        <span
          className="tap-trace-row-bar"
          style={{ left: `${left}%`, width: `${width}%` }}
        />
      </span>
      <span className="tap-trace-row-duration">{span.durationMs}ms</span>
      <span className="tap-trace-row-actions">
        {modelCallId !== null ? (
          <Button type="link" onClick={() => onOpenModelCall(modelCallId)}>
            {locale === "zh" ? "查看调用" : "View call"}
          </Button>
        ) : null}
      </span>
      {chunkIds.length > 0 ? (
        <ul className="tap-trace-hit-chunks">
          {chunkIds.map((chunkId, index) => {
            const documentId = documentIds[index] ?? chunkId;
            return (
              <li key={chunkId}>
                <a
                  role="link"
                  href={`#chunk-${encodeURIComponent(chunkId)}`}
                  onClick={(event) => {
                    event.preventDefault();
                    onOpenDocument(documentId);
                  }}
                >
                  {chunkId}
                </a>
              </li>
            );
          })}
        </ul>
      ) : null}
    </li>
  );
}
