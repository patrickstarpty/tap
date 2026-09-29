import {
  DocumentReview,
  useDocumentReview,
} from "./prototype/DocumentReview";
import { KnowledgeAnswer } from "./prototype/answer/KnowledgeAnswer";
import {
  CitationPanel,
  type OpenCitation,
} from "./prototype/answer/CitationPanel";
import { decideAnswerOutcome } from "./prototype/answer/answerOutcome";
import { takePrototypeFault } from "./prototype/prototypeFaults";
import { CodeOutlined, FileTextOutlined } from "@ant-design/icons";
import { Button } from "antd";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import { TapperChat } from "./prototype/TapperChat";
import { TapperFloatingAssistant } from "./prototype/TapperFloatingAssistant";
import { ContextualAssistantResponse } from "./prototype/ContextualAssistantResponse";
import {
  createFloatingAssistantReply,
  getFloatingAssistantContext,
  type FloatingAssistantContext,
} from "./prototype/floatingAssistantModel";
import {
  createBlankAutomation,
  createGeneratedAutomation,
  createInitialArtifactState,
  createLifeAutomation,
  createLifeTestPlan,
} from "../../legacy/artifacts/fixtures";
import type {
  Automation as AutomationAsset,
  AutomationRun,
  AutomationType,
  ExecutionTarget,
} from "../../legacy/artifacts/model";
import {
  loadPrototypeSnapshot,
  PROTOTYPE_SNAPSHOT_VERSION,
  writePrototypeSnapshot,
} from "./prototype/artifacts/persistence";
import {
  artifactReducer,
  createSimulatedRun,
} from "../../legacy/artifacts/state";
import {
  AutomationWorkspace,
  type AutomationWorkspaceView,
} from "../../legacy/automation/AutomationWorkspace";
import {
  CatalogWorkspace,
  type CatalogDraft,
} from "./prototype/CatalogWorkspace";
import { PROTOTYPE_COPY, type PrototypeCopy } from "./prototype/copy";
import { KnowledgeSourcesPanel } from "./prototype/KnowledgeSourcesPanel";
import { SAMPLE_REPRESENTATIVE_SOURCES } from "./prototype/sampleKnowledge";
import { SAMPLE_FILES } from "./prototype/sampleFiles";
import { LibraryWorkspace } from "./prototype/LibraryWorkspace";
import {
  resolveComposerAttachments,
  type ComposerAttachment,
} from "./prototype/composerAttachments";
import {
  appendTurn,
  createConversation,
  detectAutomationType,
  detectIntent,
  isGenerating,
  type AssistantTurn,
  type CatalogItem,
  type CodexModelId,
  type Conversation,
  type LibrarySource,
  type Locale,
  type ProductModule,
} from "./prototype/model";
import { PanelToggleIcon } from "./prototype/PanelToggleIcon";
import { PrototypeSidebar } from "./prototype/PrototypeSidebar";
import { TestManagementWorkspace } from "./prototype/testManagement/TestManagementWorkspace";
import { TestInsightsPrototypeWorkspace } from "../../legacy/TestAnalyticsWorkspace";
import "./TapProductPrototype.css";

function BddPreview({ copy }: { copy: PrototypeCopy }) {
  return (
    <pre className="tap-bdd-preview">
      <code>
        <span>{copy.artifacts.feature}</span>
        {"\n\n"}
        <span>
          {` ${copy.artifacts.scenario}${copy.artifacts.keywordSeparator}${copy.artifacts.completeScenario}`}
        </span>
        {"\n"}
        {`   ${copy.artifacts.given} ${copy.artifacts.completeGiven}\n`}
        {`   ${copy.artifacts.when} ${copy.artifacts.completeWhen}\n`}
        {`   ${copy.artifacts.then} ${copy.artifacts.completeThen}\n\n`}
        <span>
          {` ${copy.artifacts.scenario}${copy.artifacts.keywordSeparator}${copy.artifacts.disclosureScenario}`}
        </span>
        {"\n"}
        {`   ${copy.artifacts.given} ${copy.artifacts.disclosureGiven}\n`}
        {`   ${copy.artifacts.when} ${copy.artifacts.disclosureWhen}\n`}
        {`   ${copy.artifacts.then} ${copy.artifacts.disclosureThen}\n\n`}
        <span>
          {` ${copy.artifacts.scenario}${copy.artifacts.keywordSeparator}${copy.artifacts.highCoverageScenario}`}
        </span>
        {"\n"}
        {`   ${copy.artifacts.given} ${copy.artifacts.highCoverageGiven}\n`}
        {`   ${copy.artifacts.when} ${copy.artifacts.highCoverageWhen}\n`}
        {`   ${copy.artifacts.then} ${copy.artifacts.highCoverageThen}`}
      </code>
    </pre>
  );
}

function TurnContext({
  copy,
  turn,
}: {
  copy: PrototypeCopy;
  turn: AssistantTurn;
}) {
  if (turn.sourceReferences.length === 0) {
    return <p className="tap-context-notice">{copy.chat.noContextNotice}</p>;
  }

  return (
    <div className="tap-turn-context">
      <p className="tap-context-notice">{copy.chat.selectedContextNotice}</p>
      <ol className="tap-citation-list" aria-label={copy.chat.selectedContext}>
        {turn.sourceReferences.map((source, index) => (
          <li key={source.id}>
            <span className="tap-citation-reference">[{index + 1}]</span>
            <span>
              <strong>{source.name}</strong>
              <small>
                {source.origin === "knowledge-base"
                  ? copy.sources.knowledgeBaseDocument
                  : copy.sources.pageLocalSource}
              </small>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function AssistantResponse({
  actionCopy,
  contentCopy,
  turn,
  onImportPlan,
  onCreateTestPlanFirst,
  onGenerateLinkedAutomation,
  onSkipTestPlan,
  onChooseAutomationType,
  onOpenTestPlan,
  onOpenAutomation,
}: {
  actionCopy: PrototypeCopy;
  contentCopy: PrototypeCopy;
  turn: AssistantTurn;
  onImportPlan: () => void;
  onCreateTestPlanFirst: () => void;
  onGenerateLinkedAutomation: () => void;
  onSkipTestPlan: () => void;
  onChooseAutomationType: (type: AutomationType) => void;
  onOpenTestPlan: () => void;
  onOpenAutomation: () => void;
}) {
  if (turn.prototypeReply) {
    return <ContextualAssistantResponse turn={turn} />;
  }
  if (turn.intent === "answer") {
    return (
      <div className="tap-answer-copy">
        <p>{contentCopy.chat.answer}</p>
        <TurnContext copy={contentCopy} turn={turn} />
      </div>
    );
  }

  if (turn.intent === "test-plan") {
    return (
      <article
        className="tap-generated-artifact"
        aria-label={contentCopy.artifacts.bddPlanLabel}
      >
        <div className="tap-artifact-heading">
          <span className="tap-artifact-icon">
            <FileTextOutlined aria-hidden="true" />
          </span>
          <div>
            <strong>{contentCopy.artifacts.bddPlanReady}</strong>
            <span>{contentCopy.artifacts.scenariosDraft}</span>
          </div>
        </div>
        <BddPreview copy={contentCopy} />
        <TurnContext copy={contentCopy} turn={turn} />
        <div className="tap-artifact-actions">
          <Button type="primary" onClick={onImportPlan}>
            {actionCopy.testManagement.importToTestPlan}
          </Button>
        </div>
      </article>
    );
  }

  const workflow = turn.automationWorkflow ?? {
    stage: "ask-test-plan" as const,
    testPlanId: null,
    automationId: null,
    automationType: null,
  };
  const isChinese = turn.locale === "zh";

  return (
    <article
      className="tap-generated-artifact"
      aria-label={contentCopy.artifacts.automationLabel}
    >
      <div className="tap-artifact-heading">
        <span className="tap-artifact-icon">
          <CodeOutlined aria-hidden="true" />
        </span>
        <div>
          <strong>
            {workflow.stage === "ask-test-plan"
              ? isChinese
                ? "先创建测试计划吗？"
                : "Create a Test Plan first?"
              : workflow.stage === "choose-automation-type"
                ? isChinese
                  ? "选择 Web 或 Mobile"
                  : "Choose Web or Mobile"
                : contentCopy.artifacts.automationReady}
          </strong>
          <span>
            {workflow.stage === "ask-test-plan"
              ? isChinese
                ? "先建立业务测试意图，再生成可执行自动化"
                : "Define the business test intent before generating executable automation"
              : workflow.stage === "choose-automation-type"
                ? isChinese
                  ? "Tapper 无法可靠判断执行渠道，请确认自动化类型"
                  : "Tapper could not reliably infer the execution channel"
                : contentCopy.artifacts.automationSummary}
          </span>
        </div>
      </div>
      <TurnContext copy={contentCopy} turn={turn} />
      {workflow.stage === "ask-test-plan" ? (
        <div className="tap-artifact-actions">
          <Button onClick={onSkipTestPlan}>
            {isChinese ? "暂不创建测试计划" : "Skip Test Plan"}
          </Button>
          <Button type="primary" onClick={onCreateTestPlanFirst}>
            {isChinese ? "先创建测试计划" : "Create Test Plan first"}
          </Button>
        </div>
      ) : null}
      {workflow.stage === "review-test-plan" ? (
        <>
          <div className="tap-workflow-asset-card">
            <FileTextOutlined aria-hidden="true" />
            <span>
              <strong>
                {isChinese ? "测试计划已就绪" : "Test Plan ready"}
              </strong>
              <small>
                {workflow.testPlanId} · {contentCopy.artifacts.scenariosDraft}
              </small>
            </span>
            <Button onClick={onOpenTestPlan}>
              {isChinese ? "查看测试计划" : "Review Test Plan"}
            </Button>
          </div>
          <BddPreview copy={contentCopy} />
          <div className="tap-artifact-actions">
            <Button type="primary" onClick={onGenerateLinkedAutomation}>
              {isChinese ? "生成关联自动化" : "Generate linked automation"}
            </Button>
          </div>
        </>
      ) : null}
      {workflow.stage === "choose-automation-type" ? (
        <>
          {workflow.testPlanId === null ? null : (
            <div className="tap-workflow-asset-card">
              <FileTextOutlined aria-hidden="true" />
              <span>
                <strong>Test Plan</strong>
                <small>{workflow.testPlanId}</small>
              </span>
            </div>
          )}
          <div className="tap-artifact-actions">
            <Button onClick={() => onChooseAutomationType("mobile")}>
              {isChinese ? "创建 Mobile 自动化" : "Create Mobile automation"}
            </Button>
            <Button
              type="primary"
              onClick={() => onChooseAutomationType("web")}
            >
              {isChinese ? "创建 Web 自动化" : "Create Web automation"}
            </Button>
          </div>
        </>
      ) : null}
      {workflow.stage === "ready-linked" ||
      workflow.stage === "ready-unlinked" ? (
        <>
          <BddPreview copy={contentCopy} />
          <div className="tap-automation-summary">
            <span>
              {workflow.automationType === "mobile" ? "Wait" : "Navigate"}
            </span>
            <span>Click</span>
            <span>Send keys</span>
            <span>Assert</span>
          </div>
          <div className="tap-workflow-assets">
            {workflow.testPlanId === null ? null : (
              <div className="tap-workflow-asset-card">
                <FileTextOutlined aria-hidden="true" />
                <span>
                  <strong>Test Plan</strong>
                  <small>
                    {workflow.testPlanId} ·{" "}
                    {contentCopy.artifacts.scenariosDraft}
                  </small>
                </span>
                <Button onClick={onOpenTestPlan}>
                  {isChinese ? "打开测试计划" : "Open Test Plan"}
                </Button>
              </div>
            )}
            <div className="tap-workflow-asset-card">
              <CodeOutlined aria-hidden="true" />
              <span>
                <strong>Automation</strong>
                <small>
                  {workflow.automationId} ·{" "}
                  {workflow.automationType === "mobile" ? "Mobile" : "Web"} ·{" "}
                  {workflow.testPlanId === null
                    ? isChinese
                      ? "未关联"
                      : "Not linked"
                    : isChinese
                      ? "已关联测试计划"
                      : "Linked to Test Plan"}
                </small>
              </span>
              <Button type="primary" onClick={onOpenAutomation}>
                {actionCopy.lowCode.openInLowCode}
              </Button>
            </div>
          </div>
        </>
      ) : null}
    </article>
  );
}

export const BUILT_IN_AGENTS: readonly CatalogItem[] = [
  {
    id: "life-underwriting-analyst",
    kind: "agent",
    origin: "built-in",
    name: "Life Underwriting Analyst",
    description:
      "Reviews life policy evidence and explains underwriting decisions.",
    instructions:
      "Review selected evidence, identify underwriting implications, and explain the rationale.",
  },
  {
    id: "application-completeness-reviewer",
    kind: "agent",
    origin: "built-in",
    name: "Application Completeness Reviewer",
    description: "Checks life policy applications for missing information.",
    instructions:
      "Check identity, health disclosure, beneficiary, and payment details for completeness.",
  },
];

export const BUILT_IN_SKILLS: readonly CatalogItem[] = [
  {
    id: "bdd-scenario-design",
    kind: "skill",
    origin: "built-in",
    name: "BDD Scenario Design",
    description: "Turns underwriting rules into focused BDD scenarios.",
    instructions:
      "Create concise Given, When, Then scenarios grounded in the selected rules.",
  },
  {
    id: "underwriting-evidence-review",
    kind: "skill",
    origin: "built-in",
    name: "Underwriting Evidence Review",
    description: "Finds and summarizes evidence for underwriting decisions.",
    instructions:
      "Locate relevant evidence, summarize it faithfully, and retain source attribution.",
  },
];

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

type TurnContextOverride = {
  sourceIds: readonly string[];
  catalogReferences: NonNullable<AssistantTurn["catalogReferences"]>;
};

/** Time for a submitted message to be accepted before the reply starts. */
const TURN_ACCEPT_DELAY_MS = 350;

function nextNumericId(
  ids: readonly string[],
  prefix: string,
  floor: number,
): number {
  return (
    ids.reduce((maximum, id) => {
      const match = new RegExp(`^${prefix}-(\\d+)$`).exec(id);
      return match === null ? maximum : Math.max(maximum, Number(match[1]));
    }, floor) + 1
  );
}

export function TapProductPrototype() {
  const [initialSnapshot] = useState(() =>
    typeof window === "undefined"
      ? null
      : loadPrototypeSnapshot(window.localStorage),
  );
  const [locale, setLocale] = useState<Locale>("en");
  const review = useDocumentReview(locale);
  const answerTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  useEffect(() => {
    const timers = answerTimers.current;
    return () => {
      for (const timer of timers.values()) clearTimeout(timer);
    };
  }, []);
  const [activeModule, setActiveModule] = useState<ProductModule>(() =>
    (() => {
      const requested = new URLSearchParams(window.location.search).get("module");
      if (requested === "test-analytics") return "test-insights";
      if (requested && ["tapper", "agents", "skills", "library", "test-management", "low-code", "test-insights"].includes(requested))
        return requested as ProductModule;
      return initialSnapshot?.library?.open ? "library" : "tapper";
    })(),
  );
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
    () => initialSnapshot?.conversations ?? [createConversation("chat-1")],
  );
  const [activeConversationId, setActiveConversationId] = useState(
    () => initialSnapshot?.activeConversationId ?? "chat-1",
  );
  const [openCitation, setOpenCitation] = useState<OpenCitation | null>(null);
  const citationOpenSeq = useRef(0);
  const [artifactState, dispatchArtifact] = useReducer(
    artifactReducer,
    initialSnapshot?.artifacts ?? createInitialArtifactState(),
  );
  const [automationView, setAutomationView] = useState<AutomationWorkspaceView>(
    { kind: "library" },
  );
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [messageDraft, setMessageDraft] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const [stopError, setStopError] = useState<string | null>(null);
  const [composerContext, setComposerContext] =
    useState<FloatingAssistantContext | null>(null);
  const [agents, setAgents] = useState<readonly CatalogItem[]>(BUILT_IN_AGENTS);
  const [skills, setSkills] = useState<readonly CatalogItem[]>(BUILT_IN_SKILLS);
  const [localSources] = useState<
    readonly Pick<LibrarySource, "id" | "name" | "type">[]
  >(() => initialSnapshot?.library?.localSources ?? []);
  const nextConversationId = useRef(
    nextNumericId(
      (initialSnapshot?.conversations ?? [createConversation("chat-1")]).map(
        ({ id }) => id,
      ),
      "chat",
      0,
    ),
  );
  const nextTurnId = useRef(
    nextNumericId(
      (initialSnapshot?.conversations ?? []).flatMap((conversation) =>
        conversation.turns.map(({ id }) => id),
      ),
      "turn",
      0,
    ),
  );
  const nextAutomationId = useRef(
    nextNumericId(
      artifactState.automations.map(({ id }) => id),
      "AUTO",
      102,
    ),
  );
  const nextPlanId = useRef(
    nextNumericId(
      artifactState.testPlans.map(({ id }) => id),
      "TP",
      102,
    ),
  );
  const nextRunId = useRef(
    nextNumericId(
      artifactState.runs.map(({ id }) => id),
      "RUN",
      0,
    ),
  );
  const nextCatalogId = useRef(1);
  const pendingSendRef = useRef<string | null>(null);
  const [pendingSendConversationId, setPendingSendConversationId] = useState<
    string | null
  >(null);
  const [attachments, setAttachments] = useState<
    readonly ComposerAttachment[]
  >([]);
  const documentLanguageOnMount = useRef(document.documentElement.lang);
  const pendingFocusTarget = useRef<PendingFocusTarget | null>(null);

  const copy = PROTOTYPE_COPY[locale];
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
    setOpenCitation(null);
  }, [activeConversationId]);

  useEffect(() => {
    document.documentElement.lang = locale === "en" ? "en" : "zh-CN";
  }, [locale]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    writePrototypeSnapshot(window.localStorage, {
      version: PROTOTYPE_SNAPSHOT_VERSION,
      activeConversationId,
      conversations,
      artifacts: artifactState,
      library: {
        open: activeModule === "library",
        examplesLoaded: true,
        sampleLoaded: true,
        localSources,
      },
    });
  }, [
    activeConversationId,
    artifactState,
    conversations,
    activeModule,
    localSources,
  ]);

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

  const sources = useMemo<readonly LibrarySource[]>(
    () => [
      ...review.sources,
      ...SAMPLE_FILES,
      ...SAMPLE_REPRESENTATIVE_SOURCES,
      ...localSources.map((source) => ({
        ...source,
        origin: "page-local" as const,
        status: "ready" as const,
        description: copy.library.localSourceDescription,
      })),
    ],
    [copy.library.localSourceDescription, localSources, review.sources],
  );
  const openCitationPanel = useCallback(
    (citation: OpenCitation) => {
      citationOpenSeq.current += 1;
      const fullSource = sources.find(
        (source) => source.id === citation.source.id,
      );
      setOpenCitation({
        ...citation,
        source: {
          ...citation.source,
          hasNewerRevision: fullSource?.hasNewerRevision,
        },
        verificationFailed: takePrototypeFault(
          "citation-verification-failed",
        ),
      });
      if (sourcesCollapsed) expandKnowledgeSources();
    },
    [expandKnowledgeSources, sources, sourcesCollapsed],
  );
  const openCitationOriginal = useCallback(
    (sourceId: string) => {
      setActiveModule("library");
      if (review.sources.some((source) => source.id === sourceId)) {
        review.inspect(sourceId);
      }
    },
    [review],
  );
  const activeConversation =
    conversations.find(
      (conversation) => conversation.id === activeConversationId,
    ) ?? conversations[0]!;
  const resolvedAttachments = resolveComposerAttachments(
    attachments,
    sources,
  );
  const readyAttachmentKey = resolvedAttachments.ready
    .map(({ conversationId, sourceId }) => `${conversationId}:${sourceId}`)
    .join("|");
  useEffect(() => {
    if (resolvedAttachments.ready.length === 0) return;
    const ready = resolvedAttachments.ready;
    setConversations((current) =>
      current.map((conversation) => {
        const sourceIds = ready
          .filter((item) => item.conversationId === conversation.id)
          .map(({ sourceId }) => sourceId)
          .filter((id) => !conversation.selectedSourceIds.includes(id));
        return sourceIds.length === 0
          ? conversation
          : {
              ...conversation,
              selectedSourceIds: [...conversation.selectedSourceIds, ...sourceIds],
            };
      }),
    );
    setAttachments((current) =>
      current.filter(
        (item) =>
          !ready.some(
            (readyItem) =>
              readyItem.conversationId === item.conversationId &&
              readyItem.sourceId === item.sourceId,
          ),
      ),
    );
    // The key captures every newly published attachment.
  }, [readyAttachmentKey]);
  const floatingContext = getFloatingAssistantContext({
    activeModule,
    selectedPlanId,
    automationView,
    artifacts: artifactState,
    locale,
  });

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

  const createNewChat = () => {
    setMessageDraft("");
    setComposerContext(null);
    const id = `chat-${nextConversationId.current++}`;
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-composer textarea",
    };
    setConversations((current) => [...current, createConversation(id)]);
    setActiveConversationId(id);
    setActiveModule("tapper");
    setSidebarCollapsed(isNarrowViewport);
  };

  const selectConversation = (conversationId: string) => {
    if (conversationId !== activeConversationId) {
      setMessageDraft("");
      setComposerContext(null);
    }
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-composer textarea",
    };
    setActiveConversationId(conversationId);
    setActiveModule("tapper");
    setSidebarCollapsed(isNarrowViewport);
  };

  const selectModule = (module: ProductModule) => {
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
    if (module === "test-management" || module === "test-insights")
      setSelectedPlanId(null);
    if (module === "low-code") setAutomationView({ kind: "library" });
    setActiveModule(module);
    setSidebarCollapsed(true);
  };

  const catalogReferencesFor = (conversation: Conversation) =>
    [...agents, ...skills]
      .filter((item) =>
        (item.kind === "agent"
          ? conversation.selectedAgentIds
          : conversation.selectedSkillIds
        ).includes(item.id),
      )
      .map(({ id, kind, name }) => ({ id, kind, name }));

  const acceptTurn = (conversationId: string, accept: () => void) => {
    pendingSendRef.current = conversationId;
    setPendingSendConversationId(conversationId);
    const timerKey = `accept-${conversationId}`;
    answerTimers.current.set(
      timerKey,
      setTimeout(() => {
        answerTimers.current.delete(timerKey);
        pendingSendRef.current = null;
        setPendingSendConversationId(null);
        accept();
      }, TURN_ACCEPT_DELAY_MS),
    );
  };

  const sendMessage = (
    prompt: string,
    override?: TurnContextOverride,
  ): boolean => {
    if (pendingSendRef.current !== null) return false;
    if (activeConversation.turns.some((turn) => isGenerating(turn)))
      return false;
    if (takePrototypeFault("send-failed")) {
      setSendError(copy.composer.sendFailed);
      return false;
    }
    setSendError(null);
    const conversationId = activeConversation.id;
    const turnLocale = locale;
    if (composerContext && override === undefined) {
      const context = composerContext;
      setComposerContext(null);
      acceptTurn(conversationId, () =>
        sendFloatingMessage(prompt, context, conversationId, turnLocale),
      );
      return true;
    }
    const intent = detectIntent(prompt);
    const selectedSourceIds =
      override?.sourceIds ?? activeConversation.selectedSourceIds;
    const sourceReferences = sources
      .filter(
        (source) =>
          source.status === "ready" && selectedSourceIds.includes(source.id),
      )
      .map(({ id, name, origin }) => ({ id, name, origin }));
    const catalogReferences =
      override?.catalogReferences ?? catalogReferencesFor(activeConversation);
    const appendToConversation = (turn: AssistantTurn) =>
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === conversationId
            ? appendTurn(conversation, {
                ...turn,
                modelId: conversation.modelId,
              })
            : conversation,
        ),
      );
    if (intent === "answer") {
      const selectedSources = sources.filter(
        (source) =>
          source.status === "ready" && selectedSourceIds.includes(source.id),
      );
      const decision = decideAnswerOutcome(prompt, selectedSources);
      const evidence = decision.evidence.map(({ id, name, origin }) => ({
        id,
        name,
        origin,
      }));
      const turnId = `turn-${nextTurnId.current++}`;
      acceptTurn(conversationId, () => {
        appendToConversation({
          id: turnId,
          intent,
          locale: turnLocale,
          modelId: activeConversation.modelId,
          prompt,
          sourceReferences: evidence,
          ...(catalogReferences.length > 0 ? { catalogReferences } : {}),
          trace: {
            searchedSources: selectedSources.length,
            matchedPassages: evidence.length * 2 + 1,
            citations: evidence.length,
          },
          retrievalLimited: decision.retrievalLimited,
          answerState: "queued",
        });
        answerTimers.current.set(
          turnId,
          setTimeout(() => {
            setConversations((current) =>
              current.map((conversation) =>
                conversation.id === conversationId
                  ? {
                      ...conversation,
                      turns: conversation.turns.map((turn) =>
                        turn.id === turnId && turn.answerState === "queued"
                          ? { ...turn, answerState: "running" }
                          : turn,
                      ),
                    }
                  : conversation,
              ),
            );
            answerTimers.current.set(
              turnId,
              setTimeout(() => {
                const interrupted = takePrototypeFault("stream-interrupted");
                const sourceChanged =
                  !interrupted &&
                  takePrototypeFault("source-version-changed");
                const finalState: NonNullable<AssistantTurn["answerState"]> =
                  interrupted
                    ? "interrupted"
                    : sourceChanged
                      ? "source-changed"
                      : decision.outcome;
                setConversations((current) =>
                  current.map((conversation) =>
                    conversation.id === conversationId
                      ? {
                          ...conversation,
                          turns: conversation.turns.map((turn) =>
                            turn.id === turnId &&
                            turn.answerState === "running"
                              ? { ...turn, answerState: finalState }
                              : turn,
                          ),
                        }
                      : conversation,
                  ),
                );
                answerTimers.current.delete(turnId);
              }, 1200),
            );
          }, 500),
        );
      });
      return true;
    }
    const turnId = `turn-${nextTurnId.current++}`;
    acceptTurn(conversationId, () =>
      appendToConversation({
        id: turnId,
        intent,
        locale: turnLocale,
        modelId: activeConversation.modelId,
        prompt,
        sourceReferences,
        catalogReferences,
        automationWorkflow:
          intent === "automation"
            ? {
                stage: "ask-test-plan",
                testPlanId: null,
                automationId: null,
                automationType: detectAutomationType(prompt),
              }
            : undefined,
      }),
    );
    return true;
  };

  const resendTurn = (turn: AssistantTurn) =>
    sendMessage(turn.prompt, {
      sourceIds: turn.sourceReferences.map(({ id }) => id),
      catalogReferences: turn.catalogReferences ?? [],
    });

  const stopTurn = (turnId: string) => {
    if (takePrototypeFault("stop-failed")) {
      setStopError(copy.composer.stopFailed);
      clearTimeout(answerTimers.current.get("stop-error"));
      answerTimers.current.set(
        "stop-error",
        setTimeout(() => {
          answerTimers.current.delete("stop-error");
          setStopError(null);
        }, 3000),
      );
      return;
    }
    clearTimeout(answerTimers.current.get(turnId));
    answerTimers.current.delete(turnId);
    setConversations((current) =>
      current.map((conversation) => ({
        ...conversation,
        turns: conversation.turns.map((item) =>
          item.id === turnId && isGenerating(item)
            ? { ...item, answerState: "canceled" }
            : item,
        ),
      })),
    );
  };

  const editTurn = (turn: AssistantTurn) => {
    setComposerContext(null);
    setMessageDraft(turn.prompt);
    const readySourceIds = new Set(
      sources
        .filter((source) => source.status === "ready")
        .map(({ id }) => id),
    );
    const catalogIds = (kind: "agent" | "skill") =>
      (turn.catalogReferences ?? [])
        .filter(
          (item) =>
            item.kind === kind &&
            (kind === "agent" ? agents : skills).some(
              (catalogItem) => catalogItem.id === item.id,
            ),
        )
        .map(({ id }) => id);
    updateActiveConversation((conversation) => ({
      ...conversation,
      selectedSourceIds: turn.sourceReferences
        .map(({ id }) => id)
        .filter((id) => readySourceIds.has(id)),
      selectedAgentIds: catalogIds("agent"),
      selectedSkillIds: catalogIds("skill"),
    }));
  };

  const renameConversation = (conversationId: string, title: string) =>
    setConversations((current) =>
      current.map((conversation) =>
        conversation.id === conversationId
          ? { ...conversation, title }
          : conversation,
      ),
    );

  const deleteConversation = (conversationId: string) => {
    const deleted = conversations.find(({ id }) => id === conversationId);
    if (deleted === undefined) return;
    for (const turn of deleted.turns) {
      clearTimeout(answerTimers.current.get(turn.id));
      answerTimers.current.delete(turn.id);
    }
    if (pendingSendRef.current === conversationId) {
      clearTimeout(answerTimers.current.get(`accept-${conversationId}`));
      answerTimers.current.delete(`accept-${conversationId}`);
      pendingSendRef.current = null;
      setPendingSendConversationId(null);
    }
    setAttachments((current) =>
      current.filter((item) => item.conversationId !== conversationId),
    );
    if (conversationId !== activeConversationId) {
      setConversations((current) =>
        current.filter(({ id }) => id !== conversationId),
      );
      return;
    }
    const id = `chat-${nextConversationId.current++}`;
    setMessageDraft("");
    setComposerContext(null);
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-composer textarea",
    };
    setConversations((current) => [
      ...current.filter(({ id: itemId }) => itemId !== conversationId),
      createConversation(id),
    ]);
    setActiveConversationId(id);
    setActiveModule("tapper");
  };

  const uploadAttachment = (file: File) => {
    // A chat attachment joins Library without interrupting the conversation.
    const sourceId = review.upload(file.name, { inspect: false });
    setAttachments((current) => [
      ...current,
      { conversationId: activeConversationId, sourceId },
    ]);
  };

  const sendFloatingMessage = (
    prompt: string,
    context: FloatingAssistantContext,
    conversationId: string,
    turnLocale: Locale,
  ) => {
    const turnId = `turn-${nextTurnId.current++}`;
    setConversations((current) =>
      current.map((conversation) =>
        conversation.id === conversationId
          ? appendTurn(conversation, {
              id: turnId,
              intent: "answer",
              locale: turnLocale,
              modelId: conversation.modelId,
              prompt,
              sourceReferences: sources
                .filter((source) =>
                  conversation.selectedSourceIds.includes(source.id),
                )
                .map(({ id, name, origin }) => ({ id, name, origin })),
              catalogReferences: catalogReferencesFor(conversation),
              pageContext: {
                label: context.label,
                summary: context.summary,
                facts: context.facts,
              },
              prototypeReply: createFloatingAssistantReply(
                prompt,
                context,
                turnLocale,
              ),
            })
          : conversation,
      ),
    );
    return turnId;
  };

  const continueFloatingConversation = (context: FloatingAssistantContext) => {
    setComposerContext(context);
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-composer textarea",
    };
    setActiveModule("tapper");
    setSidebarCollapsed(isNarrowViewport);
    if (isCompactViewport) setSourcesCollapsed(true);
  };

  const updateAutomationTurn = (
    turnId: string,
    workflow: NonNullable<AssistantTurn["automationWorkflow"]>,
  ) => {
    updateActiveConversation((conversation) => ({
      ...conversation,
      turns: conversation.turns.map((turn) =>
        turn.id === turnId ? { ...turn, automationWorkflow: workflow } : turn,
      ),
    }));
  };

  const createTestPlanFirst = (turn: AssistantTurn) => {
    const planId = `TP-${nextPlanId.current++}`;
    dispatchArtifact({
      type: "test-plan/create",
      testPlan: createLifeTestPlan(planId, null, "Tapper"),
    });
    updateAutomationTurn(turn.id, {
      stage: "review-test-plan",
      testPlanId: planId,
      automationId: null,
      automationType: turn.automationWorkflow?.automationType ?? null,
    });
  };

  const createTapperAutomation = (
    turn: AssistantTurn,
    automationType: AutomationType,
  ) => {
    const planId = turn.automationWorkflow?.testPlanId ?? null;
    const automationId = `AUTO-${nextAutomationId.current++}`;
    dispatchArtifact({
      type: "automation/create",
      automation: createLifeAutomation(
        automationId,
        planId,
        "Tapper",
        automationType,
      ),
    });
    if (planId !== null) {
      dispatchArtifact({
        type: "association/set",
        automationId,
        testPlanId: planId,
      });
    }
    updateAutomationTurn(turn.id, {
      stage: planId === null ? "ready-unlinked" : "ready-linked",
      testPlanId: planId,
      automationId,
      automationType,
    });
  };

  const generateLinkedAutomation = (turn: AssistantTurn) => {
    const planId = turn.automationWorkflow?.testPlanId;
    if (planId === undefined || planId === null) return;
    const automationType = turn.automationWorkflow?.automationType ?? null;
    if (automationType === null) {
      updateAutomationTurn(turn.id, {
        stage: "choose-automation-type",
        testPlanId: planId,
        automationId: null,
        automationType: null,
      });
      return;
    }
    createTapperAutomation(turn, automationType);
  };

  const skipTestPlan = (turn: AssistantTurn) => {
    const automationType = turn.automationWorkflow?.automationType ?? null;
    if (automationType === null) {
      updateAutomationTurn(turn.id, {
        stage: "choose-automation-type",
        testPlanId: null,
        automationId: null,
        automationType: null,
      });
      return;
    }
    createTapperAutomation(
      {
        ...turn,
        automationWorkflow: {
          ...turn.automationWorkflow!,
          testPlanId: null,
        },
      },
      automationType,
    );
  };

  const chooseAutomationType = (
    turn: AssistantTurn,
    automationType: AutomationType,
  ) => {
    createTapperAutomation(
      {
        ...turn,
        automationWorkflow: {
          stage: "choose-automation-type",
          testPlanId: turn.automationWorkflow?.testPlanId ?? null,
          automationId: null,
          automationType,
        },
      },
      automationType,
    );
  };

  const openAutomation = (turn: AssistantTurn) => {
    const automationId = turn.automationWorkflow?.automationId;
    if (automationId === undefined || automationId === null) return;
    pendingFocusTarget.current = {
      kind: "selector",
      selector: "#automation-detail-heading",
    };
    setAutomationView({ kind: "detail", automationId });
    setActiveModule("low-code");
    setSidebarCollapsed(true);
  };

  const openTestPlan = (testPlanId: string) => {
    pendingFocusTarget.current = {
      kind: "selector",
      selector: "#test-plan-detail-heading",
    };
    setSelectedPlanId(testPlanId);
    setActiveModule("test-management");
    setSidebarCollapsed(true);
  };

  const importPlan = () => {
    const existing = artifactState.testPlans.find(
      (plan) => plan.source === "Tapper",
    );
    const planId = existing?.id ?? `TP-${nextPlanId.current++}`;
    if (existing === undefined) {
      dispatchArtifact({
        type: "test-plan/create",
        testPlan: createLifeTestPlan(planId, null, "Tapper"),
      });
    }
    setSelectedPlanId(null);
    setActiveModule("test-management");
    setSidebarCollapsed(true);
  };

  const createAutomation = (draft: {
    title: string;
    goal: string;
    type: AutomationType;
    testPlanId: string | null;
    mode: "blank" | "generated";
  }) => {
    const automationId = `AUTO-${nextAutomationId.current++}`;
    const automation: AutomationAsset =
      draft.mode === "blank"
        ? createBlankAutomation({
            id: automationId,
            title: draft.title,
            goal: draft.goal,
            type: draft.type,
            testPlanId: draft.testPlanId,
          })
        : createGeneratedAutomation({
            id: automationId,
            title: draft.title,
            goal: draft.goal,
            type: draft.type,
            testPlanId: draft.testPlanId,
          });
    dispatchArtifact({ type: "automation/create", automation });
    if (draft.testPlanId !== null) {
      dispatchArtifact({
        type: "association/set",
        automationId,
        testPlanId: draft.testPlanId,
      });
    }
    setAutomationView({ kind: "detail", automationId });
  };

  const runAutomation = (
    automation: AutomationAsset,
    target: ExecutionTarget,
    triggeredFrom: AutomationRun["triggeredFrom"],
  ) => {
    const run = createSimulatedRun({
      automation,
      id: `RUN-${String(nextRunId.current++).padStart(3, "0")}`,
      target,
      triggeredFrom,
      timestamp: new Date().toISOString(),
    });
    dispatchArtifact({ type: "run/add", run });
  };

  const createCatalogItem = (kind: "agent" | "skill", draft: CatalogDraft) => {
    const item: CatalogItem = {
      id: `custom-${kind}-${nextCatalogId.current++}`,
      kind,
      origin: "custom",
      ...draft,
    };
    (kind === "agent" ? setAgents : setSkills)((current) => [...current, item]);
  };

  const updateCatalogItem = (
    kind: "agent" | "skill",
    itemId: string,
    draft: CatalogDraft,
  ) => {
    (kind === "agent" ? setAgents : setSkills)((current) =>
      current.map((item) =>
        item.id === itemId ? { ...item, ...draft } : item,
      ),
    );
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

  const addLocalSource = (source: Pick<LibrarySource, "name" | "type">) => {
    review.upload(source.name);
  };

  return (
    <div
      className={`tap-product-shell${tapperWorkspaceActive ? " tap-product-shell--tapper-workspace" : ""}${tapperSidebarOpen ? " tap-product-shell--tapper-open" : ""}`}
    >
      <DocumentReview
        review={review}
        onUse={(sourceId) => {
          setComposerContext(null);
          updateActiveConversation((conversation) => ({
            ...conversation,
            selectedSourceIds: [sourceId],
          }));
          setActiveModule("tapper");
          setSidebarCollapsed(isNarrowViewport);
        }}
      />
      <PrototypeSidebar
        activeConversationId={activeConversationId}
        activeModule={activeModule}
        collapsed={sidebarCollapsed}
        conversations={conversations}
        copy={copy}
        locale={locale}
        onLocaleChange={setLocale}
        onModuleChange={selectModule}
        onNewChat={createNewChat}
        onDeleteConversation={deleteConversation}
        onRenameConversation={renameConversation}
        onSelectConversation={selectConversation}
        onToggleCollapsed={
          sidebarCollapsed ? expandTapperSidebar : dismissTapperSidebar
        }
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
              agents={agents}
              conversation={activeConversation}
              copy={copy}
              isInert={compactSourcesDrawerOpen}
              message={messageDraft}
              onMessageChange={(value) => {
                setMessageDraft(value);
                setSendError(null);
              }}
              pageContext={composerContext ?? undefined}
              onClearPageContext={() => setComposerContext(null)}
              onModelChange={(modelId: CodexModelId) =>
                updateActiveConversation((conversation) => ({
                  ...conversation,
                  modelId,
                }))
              }
              onSend={(prompt) => sendMessage(prompt)}
              sendError={sendError}
              stopError={stopError}
              isSending={pendingSendConversationId === activeConversation.id}
              onStop={() => {
                const running = activeConversation.turns.find((turn) =>
                  isGenerating(turn),
                );
                if (running !== undefined) stopTurn(running.id);
              }}
              onRegenerate={resendTurn}
              onEditTurn={editTurn}
              attachments={resolvedAttachments.pending.filter(
                (item) => item.conversationId === activeConversation.id,
              )}
              onUploadFile={uploadAttachment}
              onRemoveAttachment={(sourceId) =>
                setAttachments((current) =>
                  current.filter(
                    (item) =>
                      !(
                        item.sourceId === sourceId &&
                        item.conversationId === activeConversation.id
                      ),
                  ),
                )
              }
              onReviewAttachment={(sourceId, trigger) => {
                setActiveModule("library");
                review.inspect(sourceId, trigger);
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
              renderAssistantTurn={(turn) =>
                turn.answerState ? (
                  <KnowledgeAnswer
                    turn={turn}
                    onRetry={() => resendTurn(turn)}
                    onStop={() => stopTurn(turn.id)}
                    onOpenCitation={openCitationPanel}
                  />
                ) : (
                  <AssistantResponse
                    actionCopy={copy}
                    contentCopy={PROTOTYPE_COPY[turn.locale]}
                    turn={turn}
                    onImportPlan={importPlan}
                    onCreateTestPlanFirst={() => createTestPlanFirst(turn)}
                    onGenerateLinkedAutomation={() =>
                      generateLinkedAutomation(turn)
                    }
                    onSkipTestPlan={() => skipTestPlan(turn)}
                    onChooseAutomationType={(type) =>
                      chooseAutomationType(turn, type)
                    }
                    onOpenTestPlan={() => {
                      const testPlanId = turn.automationWorkflow?.testPlanId;
                      if (testPlanId !== undefined && testPlanId !== null) {
                        openTestPlan(testPlanId);
                      }
                    }}
                    onOpenAutomation={() => openAutomation(turn)}
                  />
                )
              }
              skills={skills}
              sources={sources.filter((source) => source.status === "ready")}
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
              {openCitation !== null ? (
                <CitationPanel
                  key={`${openCitation.turnId}:${openCitation.index}:${citationOpenSeq.current}`}
                  citation={openCitation}
                  locale={locale}
                  onClose={() => setOpenCitation(null)}
                  onOpenOriginal={openCitationOriginal}
                />
              ) : (
                <KnowledgeSourcesPanel
                  copy={copy}
                  isLoading={false}
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
                  sources={sources}
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
            onCreate={(draft) => createCatalogItem("agent", draft)}
            onUpdate={(itemId, draft) =>
              updateCatalogItem("agent", itemId, draft)
            }
            onUse={(itemId) => useCatalogItem("agent", itemId)}
          />
        ) : null}
        {activeModule === "skills" ? (
          <CatalogWorkspace
            kind="skill"
            copy={copy}
            items={skills}
            onCreate={(draft) => createCatalogItem("skill", draft)}
            onUpdate={(itemId, draft) =>
              updateCatalogItem("skill", itemId, draft)
            }
            onUse={(itemId) => useCatalogItem("skill", itemId)}
          />
        ) : null}
        {activeModule === "library" ? (
          <LibraryWorkspace
            copy={copy}
            sources={sources}
            onAddSource={addLocalSource}
            onInspectSource={review.inspect}
          />
        ) : null}
        {activeModule === "test-insights" ? (
          <TestInsightsPrototypeWorkspace
            locale={locale}
            reviewPrototype
            initialPlanId={selectedPlanId ?? undefined}
          />
        ) : null}
        {activeModule === "test-management" ? (
          <TestManagementWorkspace
            state={artifactState}
            selectedPlanId={selectedPlanId}
            locale={locale}
            onOpenPlan={setSelectedPlanId}
            onBack={() => setSelectedPlanId(null)}
            onOpenAutomation={(automationId) => {
              setAutomationView({ kind: "detail", automationId });
              setActiveModule("low-code");
            }}
            onOpenObservability={(testPlanId) => {
              setSelectedPlanId(testPlanId);
              setActiveModule("test-insights");
            }}
            onLink={(automationId, testPlanId) =>
              dispatchArtifact({
                type: "association/set",
                automationId,
                testPlanId,
              })
            }
            onRun={runAutomation}
          />
        ) : null}
        {activeModule === "low-code" ? (
          <AutomationWorkspace
            productPrototype
            state={artifactState}
            view={automationView}
            locale={locale}
            onViewChange={setAutomationView}
            onUpdate={(automation) =>
              dispatchArtifact({ type: "automation/update", automation })
            }
            onCreate={createAutomation}
            onLink={(automationId, testPlanId) =>
              dispatchArtifact({
                type: "association/set",
                automationId,
                testPlanId,
              })
            }
            onOpenTestPlan={openTestPlan}
            onRun={runAutomation}
          />
        ) : null}
        {activeModule === "low-code" && automationView.kind === "library" ? (
          <span className="tap-visually-hidden" aria-live="polite">
            {artifactState.automations.length} automations
          </span>
        ) : null}
        {activeModule === "low-code" && automationView.kind === "detail" ? (
          <span className="tap-visually-hidden" aria-live="polite">
            {automationView.automationId}
          </span>
        ) : null}
        {activeModule === "test-management" && selectedPlanId !== null ? (
          <span className="tap-visually-hidden" aria-live="polite">
            {selectedPlanId}
          </span>
        ) : null}
      </main>
      <TapperFloatingAssistant
        visible={!tapperWorkspaceActive && activeModule !== "test-insights"}
        context={floatingContext}
        conversation={activeConversation}
        draft={messageDraft}
        locale={locale}
        onDraftChange={setMessageDraft}
        onSend={sendFloatingMessage}
        onContinue={continueFloatingConversation}
      />
    </div>
  );
}
