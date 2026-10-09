import { Button } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  useSourceListQuery,
  usePublishedSourcesQuery,
  useUploadSourceMutation,
  useSourceDetailQuery,
  useRetrySourceMutation,
  useDeleteSourceMutation,
} from "../../features/knowledge/api/queries";
import { useRuntimeModeQuery } from "../../features/runtime/api/queries";
import { ValidationModeBanner } from "../../features/runtime/components/ValidationModeBanner";
import { TapperChat, type ChatAttachment } from "./workspace/TapperChat";
import { CatalogWorkspace } from "./workspace/CatalogWorkspace";
import { WORKSPACE_COPY, type WorkspaceCopy } from "./workspace/copy";
import { KnowledgeSourcesPanel } from "./workspace/KnowledgeSourcesPanel";
import {
  LibraryWorkspace,
  type PublishedSourceRevision,
} from "./workspace/LibraryWorkspace";
import {
  readGraphHighlight,
  useGraphHighlightState,
} from "../../features/graph/model/highlight";
import { AccessibleDialog } from "./workspace/AccessibleDialog";
import { KnowledgeClientError } from "../../features/knowledge/api/client";
import { useOptionalKnowledgeClient } from "../../features/knowledge/api/queries";
import type { PromptSuggestionItem } from "../../features/knowledge/api/types";
import {
  aiAssetPresentation,
  useAiAssetCatalog,
} from "../../features/knowledge/api/aiAssets";
import {
  useAppendConversation,
  useCancelTurn,
  useConversationSearch,
  useDeleteConversation,
  useRenameConversation,
  useConversationCitation,
  useConversationDetail,
  useConversationEvents,
  useConversationList,
  useConversationStream,
  useCreateConversation,
  useConversationClient,
  useTurnTrace,
} from "../../features/conversations/api/queries";
import { TracePanel } from "../../features/conversations/trace/TracePanel";
import { ModelCallDrawer } from "../../features/conversations/trace/ModelCallDrawer";
import {
  parseInsightsHandoff,
  type InsightsHandoff,
  type InsightsExplanationResult,
  type ConversationClient,
} from "../../features/conversations/api/client";
import {
  createStreamState,
  isTargetTurnActive,
  latestTurnState,
  reduceStreamEvent,
} from "../../features/conversations/model/stream";
import { GroundedAnswer } from "../../features/knowledge/components/GroundedAnswer";
import { useModelCatalog } from "../../features/knowledge/api/modelCatalog";
import { CitationViewer } from "../../features/knowledge/components/CitationViewer";
import { DocumentChunks } from "../../features/knowledge/components/DocumentChunks";
import { KnowledgeReview } from "../../features/knowledge/components/KnowledgeReview";
import {
  appendTurn,
  createConversation,
  type AssistantTurn,
  type CatalogItem,
  type ModelId,
  type Conversation,
  type LibrarySource,
  type Locale,
  type ProductModule,
} from "./workspace/model";
import { PanelToggleIcon } from "./workspace/PanelToggleIcon";
import { WorkspaceSidebar } from "./workspace/WorkspaceSidebar";
import { createTestPlanClient } from "../../features/testManagement/api/client";
import { TestPlanLibrary } from "../../features/testManagement/components/TestPlanLibrary";
import { TestPlanReview } from "../../features/testManagement/components/TestPlanReview";
import "./TapperWorkspace.css";

function durableTestPlanPath(): { planId: string; revisionId: string } | null {
  if (typeof window === "undefined") return null;
  const match = /^\/test-management\/([^/]+)\/revisions\/([^/]+)\/?$/u.exec(
    window.location.pathname,
  );
  return match === null
    ? null
    : {
        planId: decodeURIComponent(match[1]!),
        revisionId: decodeURIComponent(match[2]!),
      };
}

function TurnContext({
  copy,
  turn,
}: {
  copy: WorkspaceCopy;
  turn: AssistantTurn;
}) {
  const labels =
    turn.contextLabels ?? turn.sourceReferences.map((item) => item.name);
  if (labels.length === 0) {
    return <p className="tap-context-notice">{copy.chat.noContextNotice}</p>;
  }

  if (turn.contextLabels !== undefined) {
    const sourceCount = turn.sourceReferences.length;
    const agentLabel =
      turn.agentRevisionId == null ? null : labels[sourceCount];
    const skillLabels = labels.slice(
      sourceCount + (agentLabel === null ? 0 : 1),
    );
    return (
      <details className="tap-turn-context tap-answer-context">
        <summary>
          {turn.locale === "zh"
            ? "本次回答使用的资料与配置"
            : "Sources and settings used for this answer"}
        </summary>
        {sourceCount > 0 ? (
          <div>
            <strong>
              {turn.locale === "zh" ? "知识来源" : "Knowledge sources"}
            </strong>
            <ul>
              {turn.sourceReferences.map((source) => (
                <li key={source.id}>{source.name}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {agentLabel ? (
          <p>
            <strong>Agent:</strong> {agentLabel}
          </p>
        ) : null}
        {skillLabels.length > 0 ? (
          <p>
            <strong>Skills:</strong> {skillLabels.join(", ")}
          </p>
        ) : null}
      </details>
    );
  }

  return (
    <div className="tap-turn-context">
      <p className="tap-context-notice">{copy.chat.selectedContextNotice}</p>
      <ol className="tap-citation-list" aria-label={copy.chat.selectedContext}>
        {labels.map((label, index) => (
          <li key={`${label}-${index}`}>
            <span className="tap-citation-reference">[{index + 1}]</span>
            <span>
              <strong>{label}</strong>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

type ActivityEvent = {
  eventType: string;
  payload: { [key: string]: unknown };
};

export function AnswerActivity({
  events,
  locale,
  sourceCount,
  shownCitationCount,
}: {
  events: readonly ActivityEvent[];
  locale: "en" | "zh";
  sourceCount: number;
  shownCitationCount: number;
}) {
  const context = events.find(
    (event) => event.eventType === "context.assembled",
  );
  const stage = events.find(
    (event) =>
      event.eventType === "stage.completed" &&
      event.payload.stage === "knowledge.answer",
  );
  const hits = events.find(
    (event) => event.eventType === "retrieval.hits_ready",
  );
  const citations = events.filter(
    (event) => event.eventType === "citation.resolved",
  ).length;
  const assembledCount = sourceCount;
  const answerRecorded = events.some(
    (event) => event.eventType === "answer.delta",
  );
  const rows = [
    assembledCount > 0
      ? context
        ? locale === "zh"
          ? `整理 ${assembledCount} 份已选来源`
          : `Assembled ${assembledCount} selected sources`
        : locale === "zh"
          ? `本次选择 ${assembledCount} 份来源`
          : `${assembledCount} source${assembledCount === 1 ? "" : "s"} selected for this turn`
      : null,
    stage
      ? locale === "zh"
        ? "Knowledge answer 调用完成"
        : "Knowledge answer call completed"
      : answerRecorded
        ? locale === "zh"
          ? "回答正文已记录"
          : "Answer text recorded"
        : null,
    hits && typeof hits.payload.authorizedHitCount === "number"
      ? locale === "zh"
        ? `检索结果：${hits.payload.authorizedHitCount} 处授权证据`
        : `Retrieval: ${hits.payload.authorizedHitCount} authorized evidence hits`
      : null,
    citations > 0
      ? locale === "zh"
        ? `${citations} 条引用记录已解析；${shownCitationCount} 条用于展示的结论`
        : `${citations} citation records resolved; ${shownCitationCount} used by displayed claims`
      : null,
  ].filter((row): row is string => row !== null);
  if (rows.length === 0) return null;
  const compact = [
    assembledCount > 0
      ? locale === "zh"
        ? `${assembledCount} 份来源`
        : `${assembledCount} source${assembledCount === 1 ? "" : "s"}`
      : null,
    stage
      ? locale === "zh"
        ? "Knowledge answer"
        : "Knowledge answer"
      : answerRecorded
        ? locale === "zh"
          ? "回答已记录"
          : "Answer recorded"
        : null,
    shownCitationCount > 0
      ? locale === "zh"
        ? `${shownCitationCount} 处引用`
        : `${shownCitationCount} citation${shownCitationCount === 1 ? "" : "s"}`
      : null,
  ].filter((item): item is string => item !== null);
  return (
    <details className="tap-answer-activity">
      <summary>
        {locale === "zh" ? "执行记录" : "Activity"}
        {compact.length > 0 ? ` · ${compact.join(" · ")}` : ""}
      </summary>
      <ol>
        {rows.map((row) => (
          <li key={row}>{row}</li>
        ))}
      </ol>
    </details>
  );
}

export function AnswerProgress({
  locale,
  sourceCount,
  status = "running",
}: {
  locale: "en" | "zh";
  sourceCount: number;
  status?: "queued" | "running";
}) {
  const sources =
    sourceCount > 0
      ? locale === "zh"
        ? `使用 ${sourceCount} 份已选来源 · `
        : `Using ${sourceCount} selected source${sourceCount === 1 ? "" : "s"} · `
      : "";
  const action =
    status === "queued"
      ? locale === "zh"
        ? "等待开始…"
        : "Waiting to start…"
      : locale === "zh"
        ? "正在生成回答…"
        : "Generating answer…";
  return (
    <p className="tap-answer-progress" role="status">
      {sources}
      {action}
    </p>
  );
}

function InsightsExplanationPanel({
  question,
  result,
  locale,
}: {
  question: string;
  result: InsightsExplanationResult;
  locale: Locale;
}) {
  const english = locale === "en";
  const percentMetrics = new Set([
    "first_pass_rate",
    "final_pass_rate",
    "retry_recovery_rate",
    "recovery_contribution_rate",
  ]);
  return (
    <section
      className="tap-turn-context"
      role="region"
      aria-label="Insights explanation"
    >
      <h2>{english ? "Report investigation" : "报告调查"}</h2>
      <p>{question}</p>
      <p>
        {english ? "Verified Insights facts" : "已核实的 Insights 指标"} ·{" "}
        {result.queryId} · {result.metricVersion}
      </p>
      {result.asOf ? (
        <p>
          {english ? "As of" : "截至"} {new Date(result.asOf).toLocaleString()}
        </p>
      ) : null}
      <ul>
        {result.facts.map((fact) => (
          <li key={fact.metricId}>
            <strong>{fact.metricId.replaceAll("_", " ")}: </strong>
            {fact.completeness === "complete" && fact.value !== null
              ? percentMetrics.has(fact.metricId)
                ? `${(fact.value * 100).toFixed(1).replace(/\.0$/, "")}%`
                : String(fact.value)
              : english
                ? "Unavailable"
                : "不可用"}
            {fact.completeness === "complete" &&
            percentMetrics.has(fact.metricId) &&
            fact.numerator !== null &&
            fact.denominator !== null
              ? ` (${fact.numerator}/${fact.denominator})`
              : null}
            {fact.completeness !== "complete" && fact.missingReasons.length > 0
              ? ` · ${fact.missingReasons.join("; ")}`
              : null}
          </li>
        ))}
      </ul>
      {result.hypotheses.length > 0 ? (
        <div>
          <h3>{english ? "Possible associations" : "可能的关联"}</h3>
          <ul>
            {result.hypotheses.map((item, index) => (
              <li key={`${index}-${item}`}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {result.evidenceExcerpts.length > 0 ? (
        <div>
          <h3>{english ? "Referenced evidence" : "引用依据"}</h3>
          <ul>
            {result.evidenceExcerpts.map((item) => (
              <li key={item.citationId}>
                <strong>[{item.citationId}]</strong> <q>{item.text}</q>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {result.missingInformation.length > 0 ? (
        <div>
          <h3>{english ? "Missing information" : "缺失信息"}</h3>
          <ul>
            {result.missingInformation.map((item, index) => (
              <li key={`${index}-${item}`}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function DurableInsightsResponse({
  client,
  conversationId,
  turn,
}: {
  client: ConversationClient;
  conversationId: string;
  turn: AssistantTurn;
}) {
  const explanation = useQuery({
    queryKey: [
      "insights-explanation",
      client.projectId,
      conversationId,
      turn.id,
    ],
    queryFn: ({ signal }) =>
      client.getInsightsExplanation(conversationId, turn.id, signal),
    refetchInterval: (query) => {
      const value = query.state.data;
      return value !== undefined &&
        "state" in value &&
        (value.state === "queued" || value.state === "running")
        ? 1_000
        : false;
    },
    retry: false,
    refetchOnMount: "always",
  });
  if (
    explanation.isSuccess &&
    explanation.isFetchedAfterMount &&
    !explanation.isFetching &&
    explanation.data !== undefined &&
    "facts" in explanation.data
  ) {
    return (
      <InsightsExplanationPanel
        question={turn.prompt}
        result={explanation.data}
        locale={turn.locale}
      />
    );
  }
  const state =
    explanation.data !== undefined && "state" in explanation.data
      ? explanation.data.state
      : undefined;
  if (state === "canceled") {
    return (
      <p role="status">
        {turn.locale === "zh"
          ? "报告解读已停止。"
          : "Report investigation stopped."}
      </p>
    );
  }
  if (explanation.isError || state === "failed" || state === "abstained") {
    return (
      <p role="alert">
        {turn.locale === "zh"
          ? "报告解读暂不可用，请稍后重试。"
          : "The report explanation is unavailable. Please try again."}
      </p>
    );
  }
  return (
    <p role="status">
      {turn.locale === "zh"
        ? "正在核对报告和证据…"
        : "Checking the report and evidence…"}
    </p>
  );
}

const isFlowchartImage = (filename: string) =>
  /\.(png|jpe?g)$/iu.test(filename);
interface ResendContext {
  sourceIds: readonly string[];
  agentIds: readonly string[];
  skillIds: readonly string[];
}

const TERMINAL_TURN_STATUSES = [
  "completed",
  "abstained",
  "canceled",
  "failed",
] as const;

function DurableTracePanel({
  projectId,
  conversationId,
  turn,
  onOpenDocument,
  documentName,
}: {
  projectId: string | null;
  conversationId: string;
  turn: AssistantTurn;
  onOpenDocument: (documentId: string) => void;
  documentName?: (documentId: string) => string | undefined;
}) {
  const [openCallId, setOpenCallId] = useState<string | null>(null);
  const traceQuery = useTurnTrace(projectId, conversationId, turn.id, {
    enabled: turn.traceId != null,
    latestAttempt: turn.attempt ?? 1,
  });
  if (traceQuery.data === undefined) {
    return (
      <p className="tap-trace-panel tap-trace-panel-loading" role="status">
        {turn.locale === "zh" ? "调用链 · 加载中…" : "Trace · loading…"}
      </p>
    );
  }
  return (
    <>
      <TracePanel
        trace={traceQuery.data}
        locale={turn.locale}
        onOpenModelCall={setOpenCallId}
        onOpenDocument={onOpenDocument}
        documentName={documentName}
      />
      <ModelCallDrawer
        projectId={projectId}
        callId={openCallId}
        locale={turn.locale}
        onClose={() => setOpenCallId(null)}
      />
    </>
  );
}

function AssistantResponse({
  contentCopy,
  turn,
  insightsClient,
  conversationId,
  projectId = null,
  onOpenCitation,
  onOpenDocument,
  documentName,
  onRetryConversation,
  onResend,
  onGenerateTestPlan,
  activityEvents = [],
}: {
  contentCopy: WorkspaceCopy;
  turn: AssistantTurn;
  insightsClient?: ConversationClient | null;
  conversationId?: string;
  projectId?: string | null;
  onOpenCitation: (citationId: string, trigger: HTMLElement) => void;
  onOpenDocument?: (documentId: string) => void;
  documentName?: (documentId: string) => string | undefined;
  onRetryConversation: () => void;
  /** Sends the same question and context again as a new turn. */
  onResend?: () => void;
  onGenerateTestPlan?: () => void;
  activityEvents?: readonly ActivityEvent[];
}) {
  if (turn.intent === "answer") {
    if (
      turn.insightsQueryId !== undefined &&
      insightsClient != null &&
      conversationId !== undefined
    ) {
      return (
        <DurableInsightsResponse
          client={insightsClient}
          conversationId={conversationId}
          turn={turn}
        />
      );
    }
    if (turn.insightsExplanation !== undefined) {
      return (
        <InsightsExplanationPanel
          question={turn.prompt}
          result={turn.insightsExplanation}
          locale={turn.locale}
        />
      );
    }
    if (turn.status === "canceled") {
      return (
        <div role="status">
          <p>{turn.locale === "zh" ? "已停止生成。" : "Generation stopped."}</p>
          {onResend === undefined ? null : (
            <Button size="small" onClick={onResend}>
              {contentCopy.chat.retry}
            </Button>
          )}
        </div>
      );
    }
    if (turn.status === "failed") {
      return (
        <div role="alert">
          <p>{turn.error ?? "The answer could not be generated. Try again."}</p>
          <Button size="small" onClick={onResend ?? onRetryConversation}>
            {contentCopy.chat.retry}
          </Button>
        </div>
      );
    }
    if (turn.response !== undefined && turn.response !== null) {
      return (
        <>
          {turn.status !== undefined &&
          (TERMINAL_TURN_STATUSES as readonly string[]).includes(turn.status) &&
          turn.traceId != null &&
          conversationId !== undefined ? (
            <DurableTracePanel
              projectId={projectId}
              conversationId={conversationId}
              turn={turn}
              onOpenDocument={onOpenDocument ?? (() => undefined)}
              documentName={documentName}
            />
          ) : (
            <AnswerActivity
              events={activityEvents}
              locale={turn.locale}
              sourceCount={turn.sourceReferences.length}
              shownCitationCount={
                new Set(
                  turn.response.claims.flatMap((claim) => claim.citationIds),
                ).size
              }
            />
          )}
          <GroundedAnswer
            response={turn.response}
            locale={turn.locale}
            citationNumbering="shown-order"
            onOpenCitation={onOpenCitation}
          />
          <TurnContext copy={contentCopy} turn={turn} />
          {onResend === undefined ||
          turn.status === "queued" ||
          turn.status === "running" ? null : (
            <div className="tap-assistant-actions">
              <Button size="small" onClick={onResend}>
                {contentCopy.chat.regenerate}
              </Button>
            </div>
          )}
          {onGenerateTestPlan === undefined ? null : (
            <div className="tap-artifact-actions">
              <Button type="primary" onClick={onGenerateTestPlan}>
                {turn.locale === "zh"
                  ? "生成测试计划草稿"
                  : "Generate Test Plan draft"}
              </Button>
            </div>
          )}
        </>
      );
    }
    if (turn.error !== null && turn.error !== undefined) {
      return (
        <div role="alert">
          <p>{turn.error}</p>
          <Button size="small" onClick={onRetryConversation}>
            Retry
          </Button>
        </div>
      );
    }
    if (turn.status === "queued" || turn.status === "running") {
      return (
        <AnswerProgress
          locale={turn.locale}
          sourceCount={turn.sourceReferences.length}
          status={turn.status}
        />
      );
    }
    if (turn.response === null) {
      if (turn.evidenceStatus === "loading") {
        return <p role="status">Loading answer evidence…</p>;
      }
      return (
        <div role="alert">
          <p>
            {turn.error ??
              "Answer evidence is unavailable. Try loading the conversation again."}
          </p>
          <Button size="small" onClick={onRetryConversation}>
            Retry
          </Button>
        </div>
      );
    }
  }

  return null;
}

function toggleSelection(values: readonly string[], id: string): string[] {
  return values.includes(id)
    ? values.filter((value) => value !== id)
    : [...values, id];
}

const TAPPER_MODULE_FOCUS_TARGETS: Partial<Record<ProductModule, string>> = {
  agents: "#agent-heading",
  library: "#library-heading",
  skills: "#skill-heading",
};

type PendingFocusTarget = { kind: "selector"; selector: string };

function ProjectLibraryWorkspace({
  projectId,
  graphProjectId,
  locale,
  copy,
  sources,
  loadState,
  onReload,
  publishedSources = [],
  publishedSourcesLoading = false,
  onAskAboutNode,
  graphHighlight = null,
  onClearGraphHighlight,
}: {
  projectId: string;
  graphProjectId?: string;
  locale: "en" | "zh";
  copy: WorkspaceCopy;
  sources: readonly LibrarySource[];
  loadState: "loading" | "loaded" | "error";
  onReload: () => void;
  publishedSources?: readonly PublishedSourceRevision[];
  publishedSourcesLoading?: boolean;
  onAskAboutNode?: (label: string, sourceIds: string[]) => void;
  graphHighlight?: ReturnType<typeof useGraphHighlightState>[0];
  onClearGraphHighlight?: () => void;
}) {
  const upload = useUploadSourceMutation(projectId);
  const uploadIntents = useRef(new WeakMap<File, string>());
  const [inspected, setInspected] = useState<string | null>(null);
  const [reviewDocumentId, setReviewDocumentId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const detail = useSourceDetailQuery(projectId, inspected);
  const retry = useRetrySourceMutation(projectId);
  const deletion = useDeleteSourceMutation(projectId);
  const mutationIntents = useRef(new Map<string, string>());
  const intentKey = (intent: string) => {
    const key = mutationIntents.current.get(intent) ?? crypto.randomUUID();
    mutationIntents.current.set(intent, key);
    return key;
  };
  const busy = retry.isPending || deletion.isPending;
  const problem = retry.error ?? deletion.error ?? detail.error;
  const close = () => {
    if (!busy) {
      setInspected(null);
      setReviewDocumentId(null);
      setConfirmDelete(false);
      retry.reset();
      deletion.reset();
    }
  };
  return (
    <>
      <LibraryWorkspace
        graphProjectId={graphProjectId}
        locale={locale}
        copy={copy}
        sources={sources}
        loadState={loadState}
        onReload={onReload}
        publishedSources={publishedSources}
        publishedSourcesLoading={publishedSourcesLoading}
        onAskAboutNode={onAskAboutNode}
        graphHighlight={graphHighlight}
        onClearGraphHighlight={onClearGraphHighlight}
        onOpenSource={(sourceId, trigger) => {
          // The graph node detail panel's "Open original" button passes
          // its own trigger element explicitly (rather than relying on
          // `document.activeElement`, which Safari does not set on a
          // button click), so this reuses the same `onInspectSource` path
          // the Library list rows use — focus returns to that button when
          // the dialog closes in every browser, not just Chromium.
          opener.current = trigger;
          setInspected(sourceId);
        }}
        onInspectSource={(sourceId, trigger) => {
          opener.current = trigger;
          setInspected(sourceId);
        }}
        onAddSource={async (file, settings) => {
          const idempotencyKey =
            uploadIntents.current.get(file) ?? crypto.randomUUID();
          uploadIntents.current.set(file, idempotencyKey);
          await upload.mutateAsync({
            file,
            settings,
            onProgress: () => undefined,
            idempotencyKey,
          });
          uploadIntents.current.delete(file);
        }}
      />
      {inspected !== null && (
        <AccessibleDialog
          ariaLabel={
            detail.data?.name ??
            sources.find((source) => source.id === inspected)?.name ??
            copy.sources.heading
          }
          className="tap-add-source-dialog tap-review-dialog"
          opener={opener.current}
          onClose={close}
        >
          <h2>{detail.data?.name ?? copy.sources.heading}</h2>
          <Button onClick={close} disabled={busy}>
            {copy.sources.close}
          </Button>
          {detail.isPending && <p role="status">{copy.sources.loading}</p>}
          {problem !== null && (
            <div role="alert">
              <p>{copy.sources.mutationFailed}</p>
              {problem instanceof KnowledgeClientError && (
                <small>
                  {problem.code} · {problem.correlationId}
                </small>
              )}
              <Button
                onClick={() => {
                  void detail.refetch();
                }}
                disabled={busy}
              >
                {copy.sources.retry}
              </Button>
            </div>
          )}
          {detail.data !== undefined && (
            <>
              <p>
                {detail.data.documentCount} {copy.sources.documents} ·{" "}
                {detail.data.readyCount} {copy.sources.ready} ·{" "}
                {detail.data.failedCount} {copy.sources.failed}
              </p>
              <ul>
                {detail.data.documents.items.map((item) => (
                  <li key={item.documentId}>
                    <strong>{item.filename}</strong>
                    <p>
                      {item.status} · {item.stage}
                    </p>
                    <small>{item.revisionId}</small>
                    {/* Flowchart images keep review: model-read arrows need a person to correct and publish them. */}
                    <Button
                      aria-label={`${isFlowchartImage(item.filename) ? "审核流程图" : "管理切片"} ${item.filename}`}
                      aria-expanded={reviewDocumentId === item.documentId}
                      onClick={() =>
                        setReviewDocumentId((current) =>
                          current === item.documentId ? null : item.documentId,
                        )
                      }
                    >
                      {isFlowchartImage(item.filename)
                        ? "审核流程图"
                        : "管理切片"}
                    </Button>
                    {reviewDocumentId === item.documentId ? (
                      isFlowchartImage(item.filename) ? (
                        <KnowledgeReview
                          documentId={item.documentId}
                          sourceRevisionId={item.revisionId}
                        />
                      ) : (
                        <DocumentChunks
                          projectId={projectId}
                          documentId={item.documentId}
                        />
                      )
                    ) : null}
                    {item.errorCode != null && <p>{item.errorCode}</p>}
                    {item.status === "failed" && (
                      <Button
                        disabled={busy}
                        aria-label={`${copy.sources.retryDocument} ${item.filename}`}
                        onClick={() => {
                          const intent = `retry:${inspected}:${item.documentId}:${item.revisionId}:${item.attempt}`;
                          void retry
                            .mutateAsync({
                              sourceId: inspected,
                              request: {
                                documentId: item.documentId,
                                revisionId: item.revisionId,
                                expectedAttempt: item.attempt,
                              },
                              idempotencyKey: intentKey(intent),
                            })
                            .then(() => {
                              mutationIntents.current.delete(intent);
                            })
                            .catch(() => undefined);
                        }}
                      >
                        {copy.sources.retryDocument}
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
              {confirmDelete ? (
                <div>
                  <p>{copy.sources.deleteWarning}</p>
                  <Button
                    danger
                    disabled={busy}
                    onClick={() => {
                      const intent = `delete:${inspected}`;
                      void deletion
                        .mutateAsync({
                          sourceId: inspected,
                          idempotencyKey: intentKey(intent),
                        })
                        .then(() => {
                          mutationIntents.current.delete(intent);
                          setInspected(null);
                          setConfirmDelete(false);
                        })
                        .catch(() => undefined);
                    }}
                  >
                    {copy.sources.confirmDelete}
                  </Button>
                  <Button
                    disabled={busy}
                    onClick={() => setConfirmDelete(false)}
                  >
                    {copy.sources.cancelDelete}
                  </Button>
                </div>
              ) : (
                <Button
                  danger
                  disabled={busy}
                  onClick={() => setConfirmDelete(true)}
                >
                  {copy.sources.deleteSource}
                </Button>
              )}
            </>
          )}
        </AccessibleDialog>
      )}
    </>
  );
}

export function TapperWorkspace() {
  const runtime = useRuntimeModeQuery();
  const projectId = runtime.isSuccess ? runtime.data.projectId : null;
  const knowledgeClient = useOptionalKnowledgeClient();
  const sourcesQuery = useSourceListQuery(projectId);
  const publishedSourcesQuery = usePublishedSourcesQuery(projectId);
  const [locale, setLocale] = useState<Locale>("en");
  // Whether the very first render guessed "library" from *any* valid
  // highlight in history state, before `projectId` (below) was known —
  // read once, synchronously, so it reflects the actual state the
  // `useState` initializer below saw, not a later value.
  const optimisticLibraryGuessRef = useRef(readGraphHighlight() !== null);
  const [activeModule, setActiveModule] = useState<ProductModule>(() => {
    // `projectId` isn't resolved yet on this very first render (it comes
    // from an async runtime-mode query), so this can't filter by it the
    // way `useGraphHighlightState(projectId)` below does — it's an
    // optimistic read of *any* valid highlight, just to land on the right
    // tab a frame early. If that guess turns out wrong (the highlight was
    // for a different project, so `graphHighlight` below resolves to
    // `null`), the effect below undoes it.
    if (optimisticLibraryGuessRef.current) return "library";
    return durableTestPlanPath() !== null ? "test-management" : "tapper";
  });
  const [graphHighlight, clearGraphHighlight] =
    useGraphHighlightState(projectId);
  // A highlight that resolves or arrives after mount (the async filter
  // above settling, a same-tab push, or a popstate/forward-back
  // navigation) must switch to the Library module too — mirrors
  // `LibraryWorkspace`'s own effect that switches its internal tab. The
  // `else` branch is the undo for the optimistic guess above: once
  // `projectId` resolves, a highlight that turns out to belong to a
  // different project reads as `null` here — if that's what sent us to
  // "library" in the first place, fall back to the normal default module
  // instead of leaving the user stranded there with nothing to show. This
  // only ever fires once (the ref flips to `false` after handling it), so
  // it never clobbers a module the user deliberately navigated to later.
  useEffect(() => {
    if (graphHighlight !== null) {
      setActiveModule("library");
      return;
    }
    if (optimisticLibraryGuessRef.current) {
      optimisticLibraryGuessRef.current = false;
      setActiveModule((current) =>
        current === "library"
          ? durableTestPlanPath() !== null
            ? "test-management"
            : "tapper"
          : current,
      );
    }
  }, [graphHighlight]);
  const [isNarrowViewport, setIsNarrowViewport] = useState(
    () => window.matchMedia("(max-width: 640px)").matches,
  );
  const [isCompactViewport, setIsCompactViewport] = useState(
    () => window.matchMedia("(max-width: 1100px)").matches,
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.matchMedia("(max-width: 640px)").matches,
  );
  const [sourcesCollapsed, setSourcesCollapsed] = useState(
    () => window.matchMedia("(max-width: 1100px)").matches,
  );
  const [conversations, setConversations] = useState<readonly Conversation[]>(
    () => [createConversation("draft")],
  );
  const selectionProject = useRef(projectId);
  useEffect(() => {
    const changedProject = selectionProject.current !== projectId;
    selectionProject.current = projectId;
    if (!changedProject && !publishedSourcesQuery.isSuccess) return;
    const readyIds = new Set(
      (publishedSourcesQuery.data?.items ?? []).map(
        (source) => source.sourceId,
      ),
    );
    setConversations((current) => {
      let changed = false;
      const next = current.map((conversation) => {
        const selectedSourceIds = changedProject
          ? []
          : conversation.selectedSourceIds.filter((id) => readyIds.has(id));
        if (selectedSourceIds.length === conversation.selectedSourceIds.length)
          return conversation;
        changed = true;
        return { ...conversation, selectedSourceIds };
      });
      return changed ? next : current;
    });
  }, [projectId, publishedSourcesQuery.data, publishedSourcesQuery.isSuccess]);
  const [activeConversationId, setActiveConversationId] = useState("draft");
  const insightsClient = useConversationClient(projectId);
  const queryClient = useQueryClient();
  const conversationList = useConversationList(projectId);
  const conversationDetail = useConversationDetail(
    projectId,
    activeConversationId !== "draft" ? activeConversationId : null,
  );
  const modelCatalog = useModelCatalog(projectId);
  // Mirrors the composer's picker: only chat models with structured output are selectable.
  const selectableModelAliases = useMemo(
    () =>
      modelCatalog.isError || modelCatalog.data === undefined
        ? null
        : new Set(
            modelCatalog.data.items
              .filter(
                (model) =>
                  model.capabilities.includes("chat") &&
                  model.capabilities.includes("structured"),
              )
              .map((model) => model.alias),
          ),
    [modelCatalog.data, modelCatalog.isError],
  );
  const [requestedStreamTarget, setRequestedStreamTarget] = useState<{
    conversationId: string;
    turnId: string;
  } | null>(null);
  const [pollConversationEvents, setPollConversationEvents] = useState(true);
  const conversationEvents = useConversationEvents(
    projectId,
    activeConversationId !== "draft" ? activeConversationId : null,
    pollConversationEvents,
  );
  const recoveredStreamState = useMemo(() => {
    let recovered = createStreamState();
    for (const event of conversationEvents.data?.items ?? []) {
      recovered = reduceStreamEvent(recovered, {
        eventId: event.eventId,
        sequence: event.sequence,
        chatId: activeConversationId,
        turnId: event.turnId,
        occurredAt: event.occurredAt,
        schemaVersion: 1,
        event: { type: event.eventType, payload: event.payload },
      });
    }
    return recovered;
  }, [activeConversationId, conversationEvents.data?.items]);
  const requestedTarget =
    requestedStreamTarget?.conversationId === activeConversationId
      ? requestedStreamTarget.turnId
      : null;
  const latestDetailTurn = conversationDetail.data?.turns.at(-1) ?? null;
  const streamTargetTurnId =
    requestedTarget ?? latestDetailTurn?.turnId ?? null;
  const detailTargetStatus =
    conversationDetail.data?.turns.find(
      (turn) => turn.turnId === streamTargetTurnId,
    )?.state ?? null;
  const detailTargetIsInsights =
    conversationDetail.data?.turns.some(
      (turn) =>
        turn.turnId === streamTargetTurnId &&
        (turn.input as { insightsQueryId?: string | null }).insightsQueryId !=
          null,
    ) ?? false;
  const shouldStartConversationStream =
    !detailTargetIsInsights &&
    (requestedTarget !== null ||
      isTargetTurnActive({
        detailStatus: detailTargetStatus,
        recoveredState: recoveredStreamState,
        streamState: createStreamState(),
        targetTurnId: streamTargetTurnId,
      }));
  const conversationStream = useConversationStream(
    projectId,
    activeConversationId !== "draft" ? activeConversationId : null,
    streamTargetTurnId,
    recoveredStreamState.lastSequence,
    shouldStartConversationStream,
  );
  const streamState = conversationStream.state;
  const hasActiveConversationTurn = isTargetTurnActive({
    detailStatus: detailTargetStatus,
    recoveredState: recoveredStreamState,
    streamState,
    targetTurnId: streamTargetTurnId,
  });
  useEffect(() => {
    setPollConversationEvents(hasActiveConversationTurn);
  }, [hasActiveConversationTurn]);
  useEffect(() => {
    if (requestedTarget !== null && !hasActiveConversationTurn)
      setRequestedStreamTarget(null);
  }, [hasActiveConversationTurn, requestedTarget]);
  const createConversationMutation = useCreateConversation(projectId);
  const appendConversationMutation = useAppendConversation(
    projectId,
    activeConversationId !== "draft" ? activeConversationId : null,
  );
  const cancelTurnMutation = useCancelTurn(
    projectId,
    activeConversationId !== "draft" ? activeConversationId : null,
  );
  const resetCancelTurn = cancelTurnMutation.reset;
  const aiAssets = useAiAssetCatalog(projectId);
  const initialDurableSelection = useRef(false);
  const [selectedDurablePlan, setSelectedDurablePlan] = useState<{
    planId: string;
    revisionId: string;
  } | null>(() => durableTestPlanPath());
  const [generationJobId, setGenerationJobId] = useState<string | null>(null);
  const [generationError, setGenerationError] = useState<string | null>(null);
  useEffect(() => {
    if (projectId === null) return;
    setGenerationJobId(
      window.sessionStorage.getItem(`tap:test-plan-generation:${projectId}`),
    );
  }, [projectId]);
  useEffect(() => {
    if (projectId === null || generationJobId === null) return;
    window.sessionStorage.setItem(
      `tap:test-plan-generation:${projectId}`,
      generationJobId,
    );
  }, [generationJobId, projectId]);
  const [agents, setAgents] = useState<readonly CatalogItem[]>([]);
  const [skills, setSkills] = useState<readonly CatalogItem[]>([]);
  const sendInFlight = useRef(false);
  const pendingSendKey = useRef<{ signature: string; key: string } | null>(
    null,
  );
  const [sendPending, setSendPending] = useState(false);
  const citationTrigger = useRef<HTMLElement | null>(null);
  const [activeCitation, setActiveCitation] = useState<{
    citation: NonNullable<AssistantTurn["response"]>["citations"][number];
    conversationId: string | null;
    generation: number;
    id: string;
    turnId: string | null;
  } | null>(null);
  const historicalCitationQuery = useConversationCitation(
    projectId,
    activeCitation?.conversationId ?? null,
    activeCitation?.turnId ?? null,
    activeCitation?.id ?? null,
    activeCitation?.generation ?? 0,
  );
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null);
  const [messageDraft, setMessageDraft] = useState("");
  const [insightsHandoff, setInsightsHandoff] =
    useState<InsightsHandoff | null>(null);
  const [insightsError, setInsightsError] = useState("");
  const pendingInsightsKey = useRef<{ signature: string; key: string } | null>(
    null,
  );
  const [localInsightsTurn, setLocalInsightsTurn] = useState<{
    conversationId: string;
    turn: AssistantTurn;
  } | null>(null);
  const appliedInsightsHandoff = useRef(false);
  useEffect(() => {
    if (
      projectId === null ||
      appliedInsightsHandoff.current ||
      messageDraft.length > 0
    )
      return;
    const handoff = parseInsightsHandoff(window.location.href, projectId);
    if (handoff === null) return;
    appliedInsightsHandoff.current = true;
    setInsightsHandoff(handoff);
    setMessageDraft(handoff.draft);
  }, [messageDraft.length, projectId]);
  const documentLanguageOnMount = useRef(document.documentElement.lang);
  const pendingFocusTarget = useRef<PendingFocusTarget | null>(null);

  const copy = WORKSPACE_COPY[locale];

  useEffect(() => {
    resetCancelTurn();
  }, [activeConversationId, resetCancelTurn]);

  useEffect(() => {
    if (aiAssets.agents.isError) setAgents([]);
    if (aiAssets.skills.isError) setSkills([]);
    if (aiAssets.agents.data !== undefined) {
      setAgents(
        aiAssets.agents.data.map((item) => {
          const presentation = aiAssetPresentation(
            "agent",
            locale,
            item.toolAllowlist,
          );
          return {
            id: item.revisionId,
            kind: "agent",
            origin: "built-in",
            name: item.displayName,
            description: presentation.description,
            instructions: presentation.instructions,
          };
        }),
      );
    }
    if (aiAssets.skills.data !== undefined) {
      setSkills(
        aiAssets.skills.data.map((item) => {
          const presentation = aiAssetPresentation(
            "skill",
            locale,
            item.applicableTasks,
          );
          return {
            id: item.revisionId,
            kind: "skill",
            origin: "built-in",
            name: item.displayName,
            description: presentation.description,
            instructions: presentation.instructions,
          };
        }),
      );
    }
    if (
      aiAssets.agents.data !== undefined ||
      aiAssets.skills.data !== undefined
    ) {
      const allowedAgents = new Set(
        (aiAssets.agents.data ?? []).map((item) => item.revisionId),
      );
      const allowedSkills = new Set(
        (aiAssets.skills.data ?? []).map((item) => item.revisionId),
      );
      setConversations((current) =>
        current.map((conversation) => ({
          ...conversation,
          selectedAgentIds: conversation.selectedAgentIds.filter((id) =>
            allowedAgents.has(id),
          ),
          selectedSkillIds: conversation.selectedSkillIds.filter((id) =>
            allowedSkills.has(id),
          ),
        })),
      );
    }
  }, [
    aiAssets.agents.data,
    aiAssets.agents.isError,
    aiAssets.skills.data,
    aiAssets.skills.isError,
    locale,
  ]);

  useEffect(() => {
    if (conversationList.data === undefined) return;
    const summaries = conversationList.data.pages.flatMap((page) => page.items);
    setConversations((current) => {
      if (
        summaries.length === 0 &&
        current.some(
          (item) => item.id === activeConversationId && item.id !== "draft",
        )
      )
        return current;
      const restored = summaries.map((summary) => {
        const existing = current.find(
          (item) => item.id === summary.conversationId,
        );
        return existing === undefined
          ? createConversation(summary.conversationId, { title: summary.title })
          : { ...existing, title: summary.title };
      });
      const draft = current.find((item) => item.id === "draft");
      return (activeConversationId === "draft" || summaries.length === 0) &&
        draft !== undefined
        ? [draft, ...restored]
        : restored;
    });
    if (!initialDurableSelection.current && summaries.length > 0) {
      setActiveConversationId(summaries[0]!.conversationId);
    }
    initialDurableSelection.current = true;
  }, [activeConversationId, conversationList.data]);

  useEffect(() => {
    if (conversationDetail.data === undefined) return;
    const turns: AssistantTurn[] = conversationDetail.data.turns.map((turn) => {
      const resolvedResources = turn.input.resolvedResources ?? [];
      const insightsQueryId = (
        turn.input as { insightsQueryId?: string | null }
      ).insightsQueryId;
      const streamed = latestTurnState(
        turn.turnId,
        recoveredStreamState,
        streamState,
      );
      return {
        id: turn.turnId,
        intent: "answer",
        locale,
        modelId: turn.input.modelAlias,
        prompt: turn.input.message,
        ...(insightsQueryId == null ? {} : { insightsQueryId }),
        sourceReferences: resolvedResources.map((item) => ({
          id: item.sourceId,
          name: item.label,
          origin: "knowledge-base" as const,
        })),
        response: streamed?.response ?? null,
        traceId: turn.traceId ?? null,
        attempt: turn.attempt,
        status: ["completed", "abstained", "canceled", "failed"].includes(
          turn.state,
        )
          ? turn.state
          : (streamed?.status ?? turn.state),
        error:
          streamed?.error ??
          (conversationEvents.isError || conversationStream.error !== null
            ? "Conversation updates stopped. Your message is saved. Check access or connection, then retry."
            : null),
        evidenceStatus:
          conversationEvents.isLoading || conversationEvents.isFetching
            ? "loading"
            : conversationEvents.isError
              ? "error"
              : streamed?.response === undefined
                ? "missing"
                : "ready",
        contextLabels: [
          ...resolvedResources.map((item) => item.label),
          ...(turn.input.agentLabel == null ? [] : [turn.input.agentLabel]),
          ...(turn.input.skillLabels ?? []),
        ].filter((label): label is string => typeof label === "string"),
        inputSnapshotDigest: turn.inputSnapshotDigest,
        answerEvidenceSnapshotDigest: turn.answerEvidenceSnapshotDigest,
        agentRevisionId: turn.input.agentRevisionId,
        skillRevisionIds: turn.input.skillRevisionIds,
      };
    });
    setConversations((current) =>
      current.map((conversation) =>
        conversation.id === conversationDetail.data.conversationId
          ? {
              ...conversation,
              turns,
              ...(conversationDetail.data.turns.at(-1) === undefined
                ? {}
                : {
                    // A model that left the catalog keeps the catalog default instead of
                    // bouncing back on every refetch.
                    ...(selectableModelAliases !== null &&
                    !selectableModelAliases.has(
                      conversationDetail.data.turns.at(-1)!.input.modelAlias,
                    )
                      ? {}
                      : {
                          modelId:
                            conversationDetail.data.turns.at(-1)!.input
                              .modelAlias,
                        }),
                    selectedSourceIds:
                      conversationDetail.data.turns
                        .at(-1)!
                        .input.resolvedResources?.map(
                          (item) => item.sourceId,
                        ) ?? [],
                    selectedAgentIds:
                      conversationDetail.data.turns.at(-1)!.input
                        .agentRevisionId === null
                        ? []
                        : [
                            conversationDetail.data.turns.at(-1)!.input
                              .agentRevisionId!,
                          ],
                    selectedSkillIds:
                      conversationDetail.data.turns.at(-1)!.input
                        .skillRevisionIds,
                  }),
            }
          : conversation,
      ),
    );
    setLocalInsightsTurn((current) =>
      current !== null &&
      current.conversationId === conversationDetail.data.conversationId &&
      conversationDetail.data.turns.some(
        (turn) => turn.turnId === current.turn.id,
      )
        ? null
        : current,
    );
  }, [
    conversationDetail.data,
    conversationEvents.data,
    conversationEvents.isError,
    conversationEvents.isFetching,
    conversationEvents.isLoading,
    conversationStream.error,
    recoveredStreamState,
    selectableModelAliases,
    streamState,
  ]);
  const tapperWorkspaceActive = [
    "tapper",
    "agents",
    "skills",
    "library",
  ].includes(activeModule);
  const tapperSidebarOpen = tapperWorkspaceActive && !sidebarCollapsed;
  const mobileTapperDrawerOpen = isNarrowViewport && tapperSidebarOpen;
  const knowledgeSourcesOpen = activeModule === "tapper" && !sourcesCollapsed;
  const compactSourcesDrawerOpen = isCompactViewport && knowledgeSourcesOpen;
  const dismissTapperSidebar = useCallback(() => {
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-panel-toggle--left-expand",
    };
    setSidebarCollapsed(true);
  }, []);
  const expandTapperSidebar = useCallback(() => {
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-panel-toggle--left-collapse",
    };
    if (isCompactViewport) setSourcesCollapsed(true);
    setSidebarCollapsed(false);
  }, [isCompactViewport]);
  const dismissKnowledgeSources = useCallback(() => {
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-panel-toggle--right-expand",
    };
    setSourcesCollapsed(true);
  }, []);
  const expandKnowledgeSources = useCallback(() => {
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-panel-toggle--right-collapse",
    };
    if (isCompactViewport) setSidebarCollapsed(true);
    setSourcesCollapsed(false);
  }, [isCompactViewport]);

  useEffect(() => {
    document.documentElement.lang = locale === "en" ? "en" : "zh-CN";
  }, [locale]);

  useEffect(
    () => () => {
      document.documentElement.lang = documentLanguageOnMount.current;
    },
    [],
  );

  useEffect(() => {
    const pendingTarget = pendingFocusTarget.current;
    if (pendingTarget === null) return;

    const target = document.querySelector<HTMLElement>(pendingTarget.selector);
    if (target !== null) {
      if (target.matches("h1, h2, h3, h4, h5, h6")) target.tabIndex = -1;
      target.focus({ preventScroll: true });
    }
    pendingFocusTarget.current = null;
  }, [activeConversationId, activeModule, sidebarCollapsed, sourcesCollapsed]);

  useEffect(() => {
    const narrowMedia = window.matchMedia("(max-width: 640px)");
    const compactMedia = window.matchMedia("(max-width: 1100px)");
    const handleNarrowChange = (event: MediaQueryListEvent) => {
      setIsNarrowViewport(event.matches);
      if (event.matches) setSidebarCollapsed(true);
    };
    const handleCompactChange = (event: MediaQueryListEvent) => {
      setIsCompactViewport(event.matches);
      if (event.matches) setSourcesCollapsed(true);
    };
    narrowMedia.addEventListener("change", handleNarrowChange);
    compactMedia.addEventListener("change", handleCompactChange);
    return () => {
      narrowMedia.removeEventListener("change", handleNarrowChange);
      compactMedia.removeEventListener("change", handleCompactChange);
    };
  }, []);

  useEffect(() => {
    if (!mobileTapperDrawerOpen && !compactSourcesDrawerOpen) return;
    const previousBodyOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (compactSourcesDrawerOpen) {
        dismissKnowledgeSources();
      } else {
        dismissTapperSidebar();
      }
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousBodyOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [
    compactSourcesDrawerOpen,
    dismissTapperSidebar,
    dismissKnowledgeSources,
    mobileTapperDrawerOpen,
  ]);

  const sourceItems = useMemo<readonly LibrarySource[]>(
    () =>
      (sourcesQuery.data?.items ?? []).map((source) => ({
        id: source.sourceId,
        name: source.name,
        origin: "knowledge-base",
        type: source.name.split(".").pop()?.toUpperCase() ?? "FILE",
        status:
          source.readyCount > 0
            ? "ready"
            : source.failedCount === source.documentCount
              ? "failed"
              : "processing",
        description: `${copy.sources.knowledgeSource} · ${
          source.readyCount > 0
            ? copy.library.ready
            : source.failedCount === source.documentCount
              ? copy.library.failed
              : copy.library.processing
        }`,
      })),
    [
      copy.library.failed,
      copy.library.processing,
      copy.library.ready,
      copy.sources.knowledgeSource,
      sourcesQuery.data?.items,
    ],
  );
  const publishedDocumentNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const source of publishedSourcesQuery.data?.items ?? []) {
      map.set(source.documentId, source.filename);
    }
    return map;
  }, [publishedSourcesQuery.data?.items]);
  const publishedItems = useMemo<readonly LibrarySource[]>(() => {
    const grouped = new Map<string, LibrarySource>();
    for (const source of publishedSourcesQuery.data?.items ?? []) {
      const prior = grouped.get(source.sourceId);
      grouped.set(source.sourceId, {
        id: source.sourceId,
        name: source.sourceName,
        origin: "knowledge-base",
        type: prior
          ? "SOURCE"
          : (source.filename.split(".").pop()?.toUpperCase() ?? "FILE"),
        status: "ready",
        description:
          source.partial || prior?.description.includes("部分范围可用")
            ? "索引就绪 · 部分范围可用"
            : "索引就绪",
      });
    }
    return [...grouped.values()];
  }, [publishedSourcesQuery.data?.items]);
  const publishedSourceRevisions = useMemo(
    () =>
      (publishedSourcesQuery.data?.items ?? []).map((item) => ({
        sourceId: item.sourceId,
        revisionId: item.revisionId,
      })),
    [publishedSourcesQuery.data?.items],
  );
  const [pendingAttachments, setPendingAttachments] = useState<
    readonly {
      id: string;
      name: string;
      sourceId: string | null;
      failed: boolean;
    }[]
  >([]);
  const chatAttachments = useMemo<readonly ChatAttachment[]>(
    () =>
      pendingAttachments.map((item) => {
        const source = sourceItems.find(
          (candidate) => candidate.id === item.sourceId,
        );
        return {
          id: item.id,
          name: item.name,
          status: item.failed
            ? "failed"
            : item.sourceId === null
              ? "uploading"
              : source?.status === "failed"
                ? "failed"
                : source?.status === "ready"
                  ? "needs_review"
                  : "processing",
        };
      }),
    [pendingAttachments, sourceItems],
  );
  useEffect(() => {
    // A chat attachment becomes turn context only after Library publication.
    const published = new Set(publishedItems.map((item) => item.id));
    const usable = pendingAttachments.filter(
      (item) => item.sourceId !== null && published.has(item.sourceId),
    );
    if (usable.length === 0) return;
    setPendingAttachments((current) =>
      current.filter((item) => !usable.some((ready) => ready.id === item.id)),
    );
    setConversations((current) =>
      current.map((conversation) =>
        conversation.id === activeConversationId
          ? {
              ...conversation,
              selectedSourceIds: [
                ...conversation.selectedSourceIds,
                ...usable
                  .map((item) => item.sourceId!)
                  .filter((id) => !conversation.selectedSourceIds.includes(id)),
              ],
            }
          : conversation,
      ),
    );
  }, [activeConversationId, pendingAttachments, publishedItems]);
  const uploadAttachment = (file: File) => {
    const id = crypto.randomUUID();
    const lowerName = file.name.toLowerCase();
    const valid =
      [".pdf", ".docx", ".md", ".txt"].some((extension) =>
        lowerName.endsWith(extension),
      ) && file.size <= 25 * 1024 * 1024;
    setPendingAttachments((current) => [
      ...current,
      { id, name: file.name, sourceId: null, failed: !valid },
    ]);
    if (!valid || knowledgeClient === null) return;
    knowledgeClient
      .uploadSource(file, () => undefined, undefined, id)
      .then((receipt) => {
        void queryClient.invalidateQueries({
          queryKey: ["knowledge", projectId, "sources"],
        });
        setPendingAttachments((current) =>
          current.map((item) =>
            item.id === id
              ? { ...item, sourceId: receipt.source.sourceId }
              : item,
          ),
        );
      })
      .catch(() =>
        setPendingAttachments((current) =>
          current.map((item) =>
            item.id === id ? { ...item, failed: true } : item,
          ),
        ),
      );
  };
  const resendContextOf = (turn: AssistantTurn): ResendContext => ({
    sourceIds: turn.sourceReferences.map((source) => source.id),
    agentIds:
      turn.agentRevisionId != null
        ? [turn.agentRevisionId]
        : (turn.catalogReferences ?? [])
            .filter((item) => item.kind === "agent")
            .map((item) => item.id),
    skillIds:
      turn.skillRevisionIds ??
      (turn.catalogReferences ?? [])
        .filter((item) => item.kind === "skill")
        .map((item) => item.id),
  });
  const activeConversation =
    conversations.find(
      (conversation) => conversation.id === activeConversationId,
    ) ?? conversations[0]!;
  const displayedConversation =
    localInsightsTurn === null ||
    localInsightsTurn.conversationId !== activeConversation.id
      ? activeConversation
      : {
          ...activeConversation,
          turns: [...activeConversation.turns, localInsightsTurn.turn],
        };
  const generateTestPlan = (
    conversation: Conversation,
    turn: AssistantTurn,
  ) => {
    if (
      projectId === null ||
      turn.response === null ||
      turn.response === undefined ||
      turn.answerEvidenceSnapshotDigest == null ||
      turn.inputSnapshotDigest === undefined ||
      turn.agentRevisionId == null ||
      turn.skillRevisionIds === undefined
    )
      return;
    const api = createTestPlanClient(projectId);
    setGenerationJobId(null);
    window.sessionStorage.removeItem(`tap:test-plan-generation:${projectId}`);
    setGenerationError(null);
    void api
      .generate(
        {
          conversationId: conversation.id,
          turnId: turn.id,
          objective: `为“${turn.prompt}”设计测试计划`,
        },
        crypto.randomUUID(),
      )
      .then((job) => {
        setGenerationJobId(job.jobId);
        setSelectedDurablePlan(null);
        setActiveModule("test-management");
        setSidebarCollapsed(true);
      })
      .catch(() => {
        setGenerationError(
          locale === "zh"
            ? "无法启动测试计划生成，请返回 Tapper 重试。"
            : "Test Plan generation could not start. Try again from Tapper.",
        );
        setSelectedDurablePlan(null);
        setActiveModule("test-management");
        setSidebarCollapsed(true);
      });
  };
  const updateActiveConversation = (
    update: (conversation: Conversation) => Conversation,
  ) => {
    setConversations((current) =>
      current.map((conversation) =>
        conversation.id === activeConversationId
          ? update(conversation)
          : conversation,
      ),
    );
  };

  // `GraphOverview` already resolves published-source revision ids to real
  // source ids (via the `publishedSources` it is given), so `sourceIds`
  // here are real source ids — no reverse mapping needed.
  const askAboutGraphNode = (label: string, sourceIds: string[]) => {
    if (sourceIds.length > 0) {
      updateActiveConversation((conversation) => ({
        ...conversation,
        selectedSourceIds: [
          ...new Set([...conversation.selectedSourceIds, ...sourceIds]),
        ],
      }));
    }
    setMessageDraft(label);
    setActiveModule("tapper");
    setSourcesCollapsed(false);
  };

  const pickSuggestion = (item: PromptSuggestionItem) => {
    setMessageDraft(item.question);
    const suggestionSourceIds = item.sources.map((source) => source.sourceId);
    updateActiveConversation((conversation) => ({
      ...conversation,
      selectedSourceIds: Array.from(
        new Set([...conversation.selectedSourceIds, ...suggestionSourceIds]),
      ),
    }));
  };

  const [historySearch, setHistorySearch] = useState("");
  const [historyError, setHistoryError] = useState<string | undefined>();
  const conversationSearch = useConversationSearch(projectId, historySearch);
  const renameConversationMutation = useRenameConversation(projectId);
  const deleteConversationMutation = useDeleteConversation(projectId);
  const historyActions = {
    labels: {
      search: copy.navigation.searchChats,
      noMatches: copy.navigation.noMatchingChats,
      moreOptions: (title: string) =>
        copy.navigation.moreOptions.replace("{title}", title),
      rename: copy.navigation.renameChat,
      delete: copy.navigation.deleteChat,
      chatName: copy.navigation.chatName,
      chatNameInvalid: copy.navigation.chatNameInvalid,
      confirmDelete: copy.navigation.confirmDeleteChat,
      deleteWarning: copy.navigation.deleteChatWarning,
      cancel: copy.navigation.cancel,
    },
    search: historySearch,
    onSearchChange: (value: string) => {
      setHistorySearch(value);
      setHistoryError(undefined);
    },
    searchResults: conversationSearch.data?.items,
    isSearching: conversationSearch.isFetching,
    error: historyError,
    onRename: async (conversationId: string, title: string) => {
      try {
        await renameConversationMutation.mutateAsync({
          conversationId,
          title,
        });
        setConversations((current) =>
          current.map((item) =>
            item.id === conversationId ? { ...item, title } : item,
          ),
        );
        setHistoryError(undefined);
        return true;
      } catch {
        setHistoryError(copy.navigation.historyActionFailed);
        return false;
      }
    },
    onDelete: async (conversationId: string) => {
      try {
        await deleteConversationMutation.mutateAsync(conversationId);
        setHistoryError(undefined);
        if (conversationId === activeConversationId) createNewChat();
        setConversations((current) =>
          current.filter((item) => item.id !== conversationId),
        );
        return true;
      } catch {
        setHistoryError(copy.navigation.historyActionFailed);
        return false;
      }
    },
  };

  const createNewChat = () => {
    setMessageDraft("");
    setInsightsHandoff(null);
    setInsightsError("");
    setLocalInsightsTurn(null);
    const id = "draft";
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-composer textarea",
    };
    // An explicit choice made before history loads must not be replaced by it.
    initialDurableSelection.current = true;
    setConversations((current) => [
      createConversation(id),
      ...current.filter((item) => item.id !== id),
    ]);
    setActiveConversationId(id);
    setActiveModule("tapper");
    setSidebarCollapsed(isNarrowViewport);
  };

  const selectConversation = (conversationId: string) => {
    // A search result can point past the loaded history pages.
    const found = conversationSearch.data?.items.find(
      (item) => item.conversationId === conversationId,
    );
    if (
      found !== undefined &&
      !conversations.some((item) => item.id === conversationId)
    ) {
      setConversations((current) => [
        ...current,
        createConversation(conversationId, { title: found.title }),
      ]);
    }
    if (conversationId !== activeConversationId) {
      setMessageDraft("");
      setInsightsHandoff(null);
      setInsightsError("");
      setLocalInsightsTurn(null);
    }
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-composer textarea",
    };
    initialDurableSelection.current = true;
    setActiveConversationId(conversationId);
    setActiveModule("tapper");
    setSidebarCollapsed(isNarrowViewport);
  };

  const selectModule = (module: ProductModule) => {
    if (
      !["tapper", "test-management", "agents", "skills", "library"].includes(
        module,
      )
    )
      return;
    if (module === "tapper") {
      if (isCompactViewport) setSourcesCollapsed(true);
      setActiveModule("tapper");
      if (isNarrowViewport) setSidebarCollapsed(true);
      return;
    }
    if (["agents", "skills", "library"].includes(module)) {
      const focusTarget = TAPPER_MODULE_FOCUS_TARGETS[module];
      if (isNarrowViewport && focusTarget !== undefined) {
        pendingFocusTarget.current = {
          kind: "selector",
          selector: focusTarget,
        };
      }
      setActiveModule(module);
      if (isNarrowViewport) setSidebarCollapsed(true);
      return;
    }
    setActiveModule(module);
    setSidebarCollapsed(true);
  };

  const sendMessage = async (
    prompt: string,
    context?: ResendContext,
  ): Promise<boolean> => {
    const selectedSourceIds =
      context?.sourceIds ?? activeConversation.selectedSourceIds;
    const selectedAgentIds =
      context?.agentIds ?? activeConversation.selectedAgentIds;
    const selectedSkillIds =
      context?.skillIds ?? activeConversation.selectedSkillIds;
    if (
      createConversationMutation.isPending ||
      appendConversationMutation.isPending ||
      sendInFlight.current
    )
      return false;
    if (insightsHandoff !== null) {
      if (insightsClient === null) return false;
      sendInFlight.current = true;
      setSendPending(true);
      setInsightsError("");
      try {
        if (knowledgeClient === null) return false;
        const published = await knowledgeClient.listPublishedSources();
        const selected = published.items.filter((item) =>
          activeConversation.selectedSourceIds.includes(item.sourceId),
        );
        const request = {
          queryId: insightsHandoff.queryId,
          resourceRefs: insightsHandoff.resourceRefs,
          question: prompt,
          ...(activeConversation.id === "draft"
            ? {}
            : { conversationId: activeConversation.id }),
          sourceRevisionIds: selected.map((item) => item.revisionId),
          documentRevisionIds: [],
        };
        const signature = JSON.stringify(request);
        const storageKey = `tap:insights-pending:${projectId}`;
        let pending = pendingInsightsKey.current;
        if (pending === null) {
          try {
            pending = JSON.parse(
              window.sessionStorage.getItem(storageKey) ?? "null",
            ) as typeof pending;
          } catch {
            pending = null;
          }
        }
        if (pending?.signature !== signature) {
          pending = { signature, key: crypto.randomUUID() };
          window.sessionStorage.setItem(storageKey, JSON.stringify(pending));
        }
        pendingInsightsKey.current = pending;
        const accepted = await insightsClient.explainInsights(
          request,
          pending.key,
        );
        window.sessionStorage.removeItem(storageKey);
        pendingInsightsKey.current = null;
        setLocalInsightsTurn({
          conversationId: accepted.conversationId,
          turn: {
            id: accepted.turnId,
            intent: "answer",
            locale,
            modelId: activeConversation.modelId,
            prompt,
            sourceReferences: selected.map((item) => ({
              id: item.sourceId,
              name: item.sourceName,
              origin: "knowledge-base" as const,
            })),
            insightsQueryId: insightsHandoff.queryId,
            status: accepted.state,
          },
        });
        setConversations((current) => [
          { ...activeConversation, id: accepted.conversationId },
          ...current.filter(
            (item) =>
              item.id !== "draft" && item.id !== accepted.conversationId,
          ),
        ]);
        setActiveConversationId(accepted.conversationId);
        void queryClient.invalidateQueries({
          queryKey: ["conversations", projectId],
        });
        const handoffUrl = new URL(window.location.href);
        for (const key of ["queryId", "resourceRef", "draft"])
          handoffUrl.searchParams.delete(key);
        window.history.replaceState(window.history.state, "", handoffUrl);
        setInsightsHandoff(null);
        return true;
      } catch (error) {
        const status = (error as { status?: number }).status;
        setInsightsError(
          status === 403
            ? "You no longer have access to this report explanation."
            : "The report explanation is unavailable. Please try again.",
        );
        return false;
      } finally {
        sendInFlight.current = false;
        setSendPending(false);
      }
    }
    sendInFlight.current = true;
    setSendPending(true);
    try {
      if (knowledgeClient === null) return false;
      const api = knowledgeClient;
      const currentPublished = await api.listPublishedSources();
      const readySourceIds = new Set(
        currentPublished.items.map((item) => item.sourceId),
      );
      const allowedAgentIds = new Set(agents.map((item) => item.id));
      const allowedSkillIds = new Set(skills.map((item) => item.id));
      const futureSourceIds = selectedSourceIds.filter((id) =>
        readySourceIds.has(id),
      );
      const currentSourceReferences = currentPublished.items
        .filter((item) => futureSourceIds.includes(item.sourceId))
        .map((item) => ({
          id: item.sourceId,
          name: item.sourceName,
          origin: "knowledge-base" as const,
        }));
      const input = {
        message: prompt,
        modelAlias: activeConversation.modelId,
        sourceRevisionIds: currentPublished.items
          .filter((item) => futureSourceIds.includes(item.sourceId))
          .map((item) => item.revisionId),
        documentRevisionIds: [],
        agentRevisionId:
          selectedAgentIds.find((id) => allowedAgentIds.has(id)) ?? null,
        skillRevisionIds: selectedSkillIds.filter((id) =>
          allowedSkillIds.has(id),
        ),
      };
      // Reuse the key while the same send is unconfirmed so a retry after an
      // ambiguous failure replays instead of creating a duplicate turn.
      const signature = JSON.stringify([activeConversation.id, input]);
      if (pendingSendKey.current?.signature !== signature) {
        pendingSendKey.current = { signature, key: crypto.randomUUID() };
      }
      const key = pendingSendKey.current.key;
      const accepted =
        activeConversation.id === "draft"
          ? await createConversationMutation.mutateAsync({
              input,
              idempotencyKey: key,
            })
          : await appendConversationMutation.mutateAsync({
              input,
              idempotencyKey: key,
            });
      pendingSendKey.current = null;
      setRequestedStreamTarget({
        conversationId: accepted.conversationId,
        turnId: accepted.turnId,
      });
      setPollConversationEvents(true);
      const optimistic = appendTurn(
        { ...activeConversation, id: accepted.conversationId },
        {
          id: accepted.turnId,
          intent: "answer",
          locale,
          modelId: activeConversation.modelId,
          prompt,
          sourceReferences: currentSourceReferences,
          contextLabels: [
            ...currentSourceReferences.map((item) => item.name),
            ...agents
              .filter((item) => selectedAgentIds.includes(item.id))
              .map((item) => item.name),
            ...skills
              .filter((item) => selectedSkillIds.includes(item.id))
              .map((item) => item.name),
          ],
          status: "queued",
        },
      );
      setConversations((current) => [
        optimistic,
        ...current.filter(
          (item) => item.id !== "draft" && item.id !== accepted.conversationId,
        ),
      ]);
      setActiveConversationId(accepted.conversationId);
      return true;
    } catch {
      return false;
    } finally {
      sendInFlight.current = false;
      setSendPending(false);
    }
  };

  const useCatalogItem = (kind: "agent" | "skill", itemId: string) => {
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-composer textarea",
    };
    updateActiveConversation((conversation) => {
      const selectedKey =
        kind === "agent" ? "selectedAgentIds" : "selectedSkillIds";
      const selectedIds = conversation[selectedKey];
      return selectedIds.includes(itemId)
        ? conversation
        : { ...conversation, [selectedKey]: [...selectedIds, itemId] };
    });
    setActiveModule("tapper");
    setSidebarCollapsed(isNarrowViewport);
  };

  return (
    <div
      className={`tap-product-shell${runtime.isSuccess ? " tap-product-shell--runtime-ready" : ""}${tapperWorkspaceActive ? " tap-product-shell--tapper-workspace" : ""}${tapperSidebarOpen ? " tap-product-shell--tapper-open" : ""}`}
    >
      <ValidationModeBanner
        state={
          runtime.isSuccess
            ? "ready"
            : runtime.isError
              ? "unavailable"
              : "connecting"
        }
      />
      <WorkspaceSidebar
        activeConversationId={activeConversationId}
        activeModule={activeModule}
        collapsed={sidebarCollapsed}
        conversations={conversations}
        copy={copy}
        locale={locale}
        onLocaleChange={setLocale}
        onModuleChange={selectModule}
        onNewChat={createNewChat}
        onSelectConversation={selectConversation}
        historyActions={historyActions}
        onToggleCollapsed={
          sidebarCollapsed ? expandTapperSidebar : dismissTapperSidebar
        }
        historyState={{
          ...(conversationList.isError
            ? { error: "Conversation history is unavailable." }
            : {}),
          hasMore: conversationList.hasNextPage,
          isLoading: conversationList.isPending,
          isLoadingMore: conversationList.isFetchingNextPage,
          onLoadMore: () => {
            if (conversationList.hasNextPage)
              void conversationList.fetchNextPage();
          },
          onRetry: () => {
            void conversationList.refetch();
          },
        }}
      />
      {mobileTapperDrawerOpen ? (
        <button
          type="button"
          className="tap-sidebar-scrim"
          aria-label={copy.navigation.closeSidebar}
          tabIndex={-1}
          onClick={dismissTapperSidebar}
        />
      ) : null}
      <main
        className="tap-product-main"
        aria-hidden={mobileTapperDrawerOpen ? true : undefined}
        inert={mobileTapperDrawerOpen ? true : undefined}
      >
        <div hidden={activeModule !== "tapper"}>
          <div
            className={`tap-tapper-layout${sourcesCollapsed ? " tap-tapper-layout--sources-collapsed" : ""}`}
          >
            {sourcesCollapsed ? (
              <button
                type="button"
                className="tap-panel-toggle tap-panel-toggle--floating tap-panel-toggle--right-expand"
                aria-controls="tap-knowledge-sources"
                aria-expanded="false"
                aria-label={copy.sources.expand}
                onClick={expandKnowledgeSources}
              >
                <PanelToggleIcon side="right" state="collapsed" />
              </button>
            ) : null}
            <TapperChat
              projectId={projectId}
              agents={agents}
              conversation={displayedConversation}
              copy={copy}
              isInert={compactSourcesDrawerOpen}
              locale={locale}
              message={messageDraft}
              onMessageChange={(value) => {
                setMessageDraft(value);
                setInsightsError("");
              }}
              onModelChange={(modelId: ModelId) =>
                updateActiveConversation((conversation) => ({
                  ...conversation,
                  modelId,
                }))
              }
              onPickSuggestion={pickSuggestion}
              onSend={(prompt) => sendMessage(prompt)}
              onEditTurn={(turn) => {
                const context = resendContextOf(turn);
                setMessageDraft(turn.prompt);
                updateActiveConversation((conversation) => ({
                  ...conversation,
                  selectedSourceIds: context.sourceIds,
                  selectedAgentIds: context.agentIds,
                  selectedSkillIds: context.skillIds,
                }));
              }}
              onUploadFile={uploadAttachment}
              attachments={chatAttachments}
              onRemoveAttachment={(attachmentId) =>
                setPendingAttachments((current) =>
                  current.filter((item) => item.id !== attachmentId),
                )
              }
              onReviewAttachment={() => setActiveModule("library")}
              sendError={insightsError}
              sending={
                sendPending ||
                createConversationMutation.isPending ||
                appendConversationMutation.isPending
              }
              cancelError={cancelTurnMutation.isError}
              onCancel={(turnId) => {
                if (!cancelTurnMutation.isPending)
                  cancelTurnMutation.mutate(turnId);
              }}
              onToggleAgent={(agentId) =>
                updateActiveConversation((conversation) => ({
                  ...conversation,
                  selectedAgentIds: toggleSelection(
                    conversation.selectedAgentIds,
                    agentId,
                  ),
                }))
              }
              onToggleSkill={(skillId) =>
                updateActiveConversation((conversation) => ({
                  ...conversation,
                  selectedSkillIds: toggleSelection(
                    conversation.selectedSkillIds,
                    skillId,
                  ),
                }))
              }
              onToggleSource={(sourceId) =>
                updateActiveConversation((conversation) => ({
                  ...conversation,
                  selectedSourceIds: toggleSelection(
                    conversation.selectedSourceIds,
                    sourceId,
                  ),
                }))
              }
              renderAssistantTurn={(turn) => (
                <AssistantResponse
                  contentCopy={WORKSPACE_COPY[turn.locale]}
                  turn={turn}
                  insightsClient={insightsClient}
                  conversationId={activeConversation.id}
                  projectId={projectId}
                  onOpenDocument={(documentId) => {
                    setSourcesCollapsed(false);
                    setActiveCitation(null);
                    setActiveDocumentId(documentId);
                  }}
                  documentName={(documentId) =>
                    publishedDocumentNames.get(documentId)
                  }
                  activityEvents={(conversationEvents.data?.items ?? []).filter(
                    (event) => event.turnId === turn.id,
                  )}
                  onOpenCitation={(citationId, trigger) => {
                    const citation = turn.response?.citations.find(
                      (item) => item.citationId === citationId,
                    );
                    if (citation !== undefined) {
                      citationTrigger.current = trigger;
                      setSourcesCollapsed(false);
                      setActiveDocumentId(null);
                      setActiveCitation((current) => ({
                        citation,
                        conversationId: activeConversation.id,
                        generation: (current?.generation ?? 0) + 1,
                        id: citationId,
                        turnId: turn.id,
                      }));
                    }
                  }}
                  onRetryConversation={() => {
                    conversationStream.retry();
                    void conversationEvents.refetch();
                    void conversationDetail.refetch();
                  }}
                  onResend={
                    turn.insightsQueryId === undefined
                      ? () =>
                          void sendMessage(turn.prompt, resendContextOf(turn))
                      : undefined
                  }
                  onGenerateTestPlan={
                    projectId !== null &&
                    turn.status === "completed" &&
                    turn.answerEvidenceSnapshotDigest != null &&
                    turn.inputSnapshotDigest !== undefined &&
                    turn.agentRevisionId != null &&
                    (turn.skillRevisionIds?.length ?? 0) > 0 &&
                    (turn.response?.citations.length ?? 0) > 0
                      ? () => generateTestPlan(activeConversation, turn)
                      : undefined
                  }
                />
              )}
              skills={skills}
              sources={sourceItems}
            />
            {compactSourcesDrawerOpen ? (
              <button
                type="button"
                className="tap-sources-scrim"
                aria-label={copy.sources.close}
                tabIndex={-1}
                onClick={dismissKnowledgeSources}
              />
            ) : null}
            <div
              className="tap-sources-shell"
              aria-hidden={sourcesCollapsed ? true : undefined}
              data-collapsed={sourcesCollapsed}
              inert={sourcesCollapsed ? true : undefined}
            >
              {activeCitation !== null ? (
                <CitationViewer
                  active={activeCitation}
                  locale={locale}
                  historicalQuery={historicalCitationQuery}
                  returnFocusTo={citationTrigger.current}
                  onClose={() => setActiveCitation(null)}
                />
              ) : activeDocumentId !== null && projectId !== null ? (
                <section
                  className="tapper-panel tap-trace-document-panel"
                  aria-labelledby="tap-trace-document-heading"
                >
                  <header className="tapper-panel-header">
                    <h2 id="tap-trace-document-heading">
                      {locale === "zh" ? "原文与切片" : "Source document"}
                    </h2>
                    <Button
                      onClick={() => setActiveDocumentId(null)}
                      aria-label={locale === "zh" ? "关闭" : "Close"}
                    >
                      {locale === "zh" ? "关闭" : "Close"}
                    </Button>
                  </header>
                  <DocumentChunks
                    projectId={projectId}
                    documentId={activeDocumentId}
                  />
                </section>
              ) : (
                <KnowledgeSourcesPanel
                  copy={copy}
                  isLoading={publishedSourcesQuery.isPending}
                  isError={publishedSourcesQuery.isError}
                  onRetry={() => {
                    void publishedSourcesQuery.refetch();
                  }}
                  onCollapse={dismissKnowledgeSources}
                  onToggleSource={(sourceId) =>
                    updateActiveConversation((conversation) => ({
                      ...conversation,
                      selectedSourceIds: toggleSelection(
                        conversation.selectedSourceIds,
                        sourceId,
                      ),
                    }))
                  }
                  selectedSourceIds={activeConversation.selectedSourceIds}
                  sources={publishedItems}
                />
              )}
            </div>
          </div>
        </div>
        {activeModule === "agents" ? (
          <CatalogWorkspace
            kind="agent"
            copy={copy}
            items={agents}
            onUse={(itemId) => useCatalogItem("agent", itemId)}
            projectId={projectId ?? undefined}
          />
        ) : null}
        {activeModule === "skills" ? (
          <CatalogWorkspace
            kind="skill"
            copy={copy}
            items={skills}
            onUse={(itemId) => useCatalogItem("skill", itemId)}
            projectId={projectId ?? undefined}
          />
        ) : null}
        {activeModule === "library" ? (
          projectId !== null ? (
            <ProjectLibraryWorkspace
              key={projectId}
              projectId={projectId}
              graphProjectId={projectId}
              locale={locale}
              copy={copy}
              sources={sourceItems}
              loadState={
                sourcesQuery.isPending
                  ? "loading"
                  : sourcesQuery.isError
                    ? "error"
                    : "loaded"
              }
              onReload={() => {
                void sourcesQuery.refetch();
              }}
              publishedSources={publishedSourceRevisions}
              publishedSourcesLoading={publishedSourcesQuery.isPending}
              onAskAboutNode={askAboutGraphNode}
              graphHighlight={graphHighlight}
              onClearGraphHighlight={clearGraphHighlight}
            />
          ) : (
            <LibraryWorkspace copy={copy} sources={sourceItems} />
          )
        ) : null}
        {activeModule === "test-management" && projectId !== null ? (
          selectedDurablePlan === null ? (
            <TestPlanLibrary
              projectId={projectId}
              locale={locale}
              generationJobId={generationJobId}
              generationError={generationError}
              onGenerateFromLatest={() => {
                const latest = [...activeConversation.turns]
                  .reverse()
                  .find(
                    (turn) =>
                      turn.status === "completed" &&
                      turn.answerEvidenceSnapshotDigest != null &&
                      turn.inputSnapshotDigest !== undefined &&
                      turn.agentRevisionId != null &&
                      (turn.skillRevisionIds?.length ?? 0) > 0 &&
                      (turn.response?.citations.length ?? 0) > 0,
                  );
                if (latest) generateTestPlan(activeConversation, latest);
              }}
              onGoTapper={() => {
                setActiveModule("tapper");
                setSidebarCollapsed(isNarrowViewport);
              }}
              onOpen={(planId, revisionId) => {
                window.history.pushState(
                  null,
                  "",
                  `/test-management/${encodeURIComponent(planId)}/revisions/${encodeURIComponent(revisionId)}`,
                );
                setSelectedDurablePlan({ planId, revisionId });
              }}
            />
          ) : (
            <TestPlanReview
              projectId={projectId}
              planId={selectedDurablePlan.planId}
              revisionId={selectedDurablePlan.revisionId}
              locale={locale}
              onBack={() => {
                window.history.pushState(null, "", "/");
                setSelectedDurablePlan(null);
              }}
              onOpenRevision={(planId, revisionId) => {
                window.history.pushState(
                  null,
                  "",
                  `/test-management/${encodeURIComponent(planId)}/revisions/${encodeURIComponent(revisionId)}`,
                );
                setSelectedDurablePlan({ planId, revisionId });
              }}
              onOpenAutomation={() => {
                window.history.pushState(
                  null,
                  "",
                  `/automation/new?testPlan=${encodeURIComponent(selectedDurablePlan.planId)}`,
                );
              }}
            />
          )
        ) : activeModule === "test-management" ? (
          <p role="status">
            Test plans are available when the TAP AI API is ready.
          </p>
        ) : null}
      </main>
    </div>
  );
}
