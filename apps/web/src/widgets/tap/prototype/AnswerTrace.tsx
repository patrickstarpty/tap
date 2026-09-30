import { useRef, useState } from "react";
import { Button } from "antd";
import { AccessibleDialog } from "../../../legacy/AccessibleDialog";
import type { Locale } from "./model";
import type { SampleTrace, TraceHitChunk, TraceSpan } from "./sampleTrace";
import "./AnswerTrace.css";

type Translate = (en: string, zh: string) => string;
type DrawerTab = "request" | "response" | "reasoning";

const SPAN_LABELS: Record<string, { en: string; zh: string }> = {
  "turn.execute": { en: "Turn execution", zh: "回合执行" },
  "chat.plan": { en: "Answer plan", zh: "回答规划" },
  "retrieval.search": { en: "Retrieval search", zh: "检索" },
  "graph.enrich": { en: "Graph enrichment", zh: "图谱扩展" },
  "citations.resolve": { en: "Citation resolution", zh: "引用解析" },
};

function spanLabel(name: string, locale: Locale): string {
  const entry = SPAN_LABELS[name];
  if (!entry) return name;
  return locale === "zh" ? entry.zh : entry.en;
}

function formatCost(trace: SampleTrace, t: Translate): string {
  if (trace.costUsd === null) return t("Cost unknown", "成本未知");
  const amount = `$${trace.costUsd.toFixed(4)}`;
  if (!trace.costIncomplete) return amount;
  return `${amount}+${t(" (cost partially unknown)", "（部分成本未知）")}`;
}

function drawerContent(
  span: TraceSpan,
  tab: DrawerTab,
  t: Translate,
): string {
  const call = span.modelCall;
  if (!call) return "";
  if (tab === "request") return call.request;
  if (tab === "response")
    return call.response ?? t("No response recorded.", "未记录返回内容。");
  return call.reasoning ?? t("No reasoning recorded.", "未记录推理内容。");
}

export function AnswerTrace({ trace }: { trace: SampleTrace }) {
  const locale = trace.locale;
  const t: Translate = (en, zh) => (locale === "zh" ? zh : en);
  const [expanded, setExpanded] = useState(false);
  const [attempt, setAttempt] = useState(
    trace.attempts[trace.attempts.length - 1],
  );
  const [chunk, setChunk] = useState<TraceHitChunk | null>(null);
  const [callSpan, setCallSpan] = useState<TraceSpan | null>(null);
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("request");
  const opener = useRef<HTMLElement | null>(null);

  const attemptSpans = trace.spans.filter((span) => span.attempt === attempt);
  const maxDuration = Math.max(...attemptSpans.map((span) => span.durationMs), 1);

  const depthOf = (span: TraceSpan): number => {
    let depth = 0;
    let current = span;
    while (current.parentSpanId) {
      const parent = attemptSpans.find(
        (candidate) => candidate.spanId === current.parentSpanId,
      );
      if (!parent) break;
      depth += 1;
      current = parent;
    }
    return depth;
  };

  return (
    <div className="tap-answer-trace">
      <Button
        type="text"
        className="tap-answer-trace-summary"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {t("Trace", "调用链")}: {(trace.totalDurationMs / 1000).toFixed(1)}s ·{" "}
        {trace.inputTokens}/{trace.outputTokens} tokens ·{" "}
        {formatCost(trace, t)} · {trace.requestedModel} →{" "}
        {trace.upstreamModel}
      </Button>
      {expanded ? (
        <div className="tap-answer-trace-body">
          {trace.attempts.length > 1 ? (
            <div className="tap-answer-trace-attempts" role="tablist">
              {trace.attempts.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={value === attempt}
                  onClick={() => setAttempt(value)}
                >
                  {t(`Attempt ${value}`, `第 ${value} 次尝试`)}
                </button>
              ))}
            </div>
          ) : null}
          <ul className="tap-answer-trace-waterfall">
            {attemptSpans.map((span) => (
              <li
                key={span.spanId}
                className={
                  span.status === "error"
                    ? "tap-trace-span tap-trace-span-error"
                    : "tap-trace-span"
                }
                style={{ paddingLeft: depthOf(span) * 16 }}
              >
                <span className="tap-trace-span-name">
                  {spanLabel(span.name, locale)}
                </span>
                <span
                  className="tap-trace-span-bar"
                  style={{
                    width: `${Math.max(4, (span.durationMs / maxDuration) * 100)}%`,
                  }}
                />
                <span className="tap-trace-span-duration">
                  {span.durationMs}ms
                </span>
                {span.modelCall ? (
                  <Button
                    type="link"
                    onClick={(event) => {
                      opener.current = event.currentTarget;
                      setCallSpan(span);
                      setDrawerTab("request");
                    }}
                  >
                    {t("View call", "查看调用")}
                  </Button>
                ) : null}
                {span.hitChunks && span.hitChunks.length > 0 ? (
                  <ul className="tap-trace-hit-chunks">
                    {span.hitChunks.map((hit) => (
                      <li key={hit.id}>
                        <Button
                          type="link"
                          onClick={(event) => {
                            opener.current = event.currentTarget;
                            setChunk(hit);
                          }}
                        >
                          {hit.sourceName}
                        </Button>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {chunk ? (
        <AccessibleDialog
          ariaLabel={t("Source citation", "原文引用")}
          className="tap-document-review tap-document-citation"
          opener={opener.current}
          onClose={() => setChunk(null)}
        >
          <header>
            <div>
              <h2>{chunk.sourceName}</h2>
            </div>
            <Button
              onClick={() => setChunk(null)}
              aria-label={t("Close citation", "关闭引用")}
            >
              {t("Close", "关闭")}
            </Button>
          </header>
          <article className="tap-document-original">
            <mark>{chunk.snippet}</mark>
          </article>
        </AccessibleDialog>
      ) : null}
      {callSpan?.modelCall ? (
        <AccessibleDialog
          ariaLabel={t("Model call detail", "模型调用详情")}
          className="tap-answer-trace-drawer"
          opener={opener.current}
          onClose={() => setCallSpan(null)}
        >
          <header>
            <h2>{callSpan.name}</h2>
            <Button
              onClick={() => setCallSpan(null)}
              aria-label={t("Close", "关闭")}
            >
              {t("Close", "关闭")}
            </Button>
          </header>
          <div role="tablist" className="tap-answer-trace-drawer-tabs">
            {(["request", "response", "reasoning"] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={drawerTab === tab}
                onClick={() => setDrawerTab(tab)}
              >
                {tab === "request"
                  ? t("Request", "请求")
                  : tab === "response"
                    ? t("Response", "返回")
                    : t("Reasoning", "推理")}
              </button>
            ))}
          </div>
          <pre>{drawerContent(callSpan, drawerTab, t)}</pre>
          <Button
            onClick={() =>
              navigator.clipboard.writeText(
                drawerContent(callSpan, drawerTab, t),
              )
            }
          >
            {t("Copy", "复制")}
          </Button>
        </AccessibleDialog>
      ) : null}
    </div>
  );
}
