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

import { useDocumentListQuery } from "../../features/knowledge/api/queries";
import { AthenaChat } from "./prototype/AthenaChat";
import {
  createBlankAutomation,
  createGeneratedAutomation,
  createInitialArtifactState,
  createLifeAutomation,
  createLifeTestPlan,
} from "./prototype/artifacts/fixtures";
import type {
  Automation as AutomationAsset,
  AutomationRun,
  AutomationType,
  ExecutionTarget,
} from "./prototype/artifacts/model";
import {
  loadPrototypeSnapshot,
  PROTOTYPE_SNAPSHOT_VERSION,
  writePrototypeSnapshot,
} from "./prototype/artifacts/persistence";
import {
  artifactReducer,
  createSimulatedRun,
} from "./prototype/artifacts/state";
import {
  AutomationWorkspace,
  type AutomationWorkspaceView,
} from "./prototype/automation/AutomationWorkspace";
import {
  CatalogWorkspace,
  type CatalogDraft,
} from "./prototype/CatalogWorkspace";
import { PROTOTYPE_COPY, type PrototypeCopy } from "./prototype/copy";
import { KnowledgeSourcesPanel } from "./prototype/KnowledgeSourcesPanel";
import { LibraryWorkspace } from "./prototype/LibraryWorkspace";
import {
  appendTurn,
  createConversation,
  detectAutomationType,
  detectIntent,
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
                  ? "Athena 无法可靠判断执行渠道，请确认自动化类型"
                  : "Athena could not reliably infer the execution channel"
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

const ATHENA_MODULE_FOCUS_TARGETS: Partial<Record<ProductModule, string>> = {
  agents: "#agent-heading",
  library: "#library-heading",
  skills: "#skill-heading",
};

type PendingFocusTarget = { kind: "selector"; selector: string };

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
  const documentsQuery = useDocumentListQuery();
  const [initialSnapshot] = useState(() =>
    typeof window === "undefined"
      ? null
      : loadPrototypeSnapshot(window.localStorage),
  );
  const [locale, setLocale] = useState<Locale>("en");
  const [activeModule, setActiveModule] = useState<ProductModule>("athena");
  const lastAthenaModule = useRef<ProductModule>("athena");
  const [isNarrowViewport, setIsNarrowViewport] = useState(
    () => window.matchMedia("(max-width: 640px)").matches,
  );
  const [isCompactViewport, setIsCompactViewport] = useState(
    () => window.matchMedia("(max-width: 820px)").matches,
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.matchMedia("(max-width: 640px)").matches,
  );
  const [sourcesCollapsed, setSourcesCollapsed] = useState(
    () => window.matchMedia("(max-width: 820px)").matches,
  );
  const [conversations, setConversations] = useState<readonly Conversation[]>(
    () => initialSnapshot?.conversations ?? [createConversation("chat-1")],
  );
  const [activeConversationId, setActiveConversationId] = useState(
    () => initialSnapshot?.activeConversationId ?? "chat-1",
  );
  const [artifactState, dispatchArtifact] = useReducer(
    artifactReducer,
    initialSnapshot?.artifacts ?? createInitialArtifactState(),
  );
  const [automationView, setAutomationView] = useState<AutomationWorkspaceView>(
    { kind: "library" },
  );
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [agents, setAgents] = useState<readonly CatalogItem[]>(BUILT_IN_AGENTS);
  const [skills, setSkills] = useState<readonly CatalogItem[]>(BUILT_IN_SKILLS);
  const [localSources, setLocalSources] = useState<
    readonly Pick<LibrarySource, "id" | "name" | "type">[]
  >([]);
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
  const nextLocalSourceId = useRef(1);
  const documentLanguageOnMount = useRef(document.documentElement.lang);
  const pendingFocusTarget = useRef<PendingFocusTarget | null>(null);

  const copy = PROTOTYPE_COPY[locale];
  const athenaWorkspaceActive = [
    "athena",
    "agents",
    "skills",
    "library",
  ].includes(activeModule);
  const athenaSidebarOpen = athenaWorkspaceActive && !sidebarCollapsed;
  const mobileAthenaDrawerOpen = isNarrowViewport && athenaSidebarOpen;
  const knowledgeSourcesOpen = activeModule === "athena" && !sourcesCollapsed;
  const compactSourcesDrawerOpen = isCompactViewport && knowledgeSourcesOpen;
  const dismissAthenaSidebar = useCallback(() => {
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-panel-toggle--left-expand",
    };
    setSidebarCollapsed(true);
  }, []);
  const expandAthenaSidebar = useCallback(() => {
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

  useEffect(() => {
    if (typeof window === "undefined") return;
    writePrototypeSnapshot(window.localStorage, {
      version: PROTOTYPE_SNAPSHOT_VERSION,
      activeConversationId,
      conversations,
      artifacts: artifactState,
    });
  }, [activeConversationId, artifactState, conversations]);

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
    const compactMedia = window.matchMedia("(max-width: 820px)");
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
    if (!mobileAthenaDrawerOpen && !compactSourcesDrawerOpen) return;
    const previousBodyOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (compactSourcesDrawerOpen) {
        dismissKnowledgeSources();
      } else {
        dismissAthenaSidebar();
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
    dismissAthenaSidebar,
    dismissKnowledgeSources,
    mobileAthenaDrawerOpen,
  ]);

  const documentSources = useMemo<readonly LibrarySource[]>(
    () =>
      (documentsQuery.data?.items ?? []).map((document) => ({
        id: document.documentId,
        name: document.filename,
        origin: "knowledge-base",
        type: document.filename.split(".").pop()?.toUpperCase() ?? "FILE",
        status:
          document.status === "ready"
            ? "ready"
            : document.status === "failed"
              ? "failed"
              : "processing",
        description: `${copy.sources.knowledgeSource} · ${
          document.status === "ready"
            ? copy.library.ready
            : document.status === "failed"
              ? copy.library.failed
              : copy.library.processing
        }`,
      })),
    [
      copy.library.failed,
      copy.library.processing,
      copy.library.ready,
      copy.sources.knowledgeSource,
      documentsQuery.data?.items,
    ],
  );
  const sources = useMemo<readonly LibrarySource[]>(
    () => [
      ...documentSources,
      ...localSources.map((source) => ({
        ...source,
        origin: "page-local" as const,
        status: "ready" as const,
        description: copy.library.localSourceDescription,
      })),
    ],
    [copy.library.localSourceDescription, documentSources, localSources],
  );
  const activeConversation =
    conversations.find(
      (conversation) => conversation.id === activeConversationId,
    ) ?? conversations[0]!;

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
    const id = `chat-${nextConversationId.current++}`;
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-composer textarea",
    };
    setConversations((current) => [...current, createConversation(id)]);
    setActiveConversationId(id);
    lastAthenaModule.current = "athena";
    setActiveModule("athena");
    setSidebarCollapsed(isNarrowViewport);
  };

  const selectConversation = (conversationId: string) => {
    pendingFocusTarget.current = {
      kind: "selector",
      selector: ".tap-composer textarea",
    };
    setActiveConversationId(conversationId);
    lastAthenaModule.current = "athena";
    setActiveModule("athena");
    setSidebarCollapsed(isNarrowViewport);
  };

  const selectModule = (module: ProductModule) => {
    if (module === "athena") {
      if (isCompactViewport) setSourcesCollapsed(true);
      setActiveModule(lastAthenaModule.current);
      setSidebarCollapsed(false);
      return;
    }
    if (["agents", "skills", "library"].includes(module)) {
      const focusTarget = ATHENA_MODULE_FOCUS_TARGETS[module];
      if (isNarrowViewport && focusTarget !== undefined) {
        pendingFocusTarget.current = {
          kind: "selector",
          selector: focusTarget,
        };
      }
      lastAthenaModule.current = module;
      setActiveModule(module);
      setSidebarCollapsed(isNarrowViewport);
      return;
    }
    if (module === "test-management") setSelectedPlanId(null);
    if (module === "low-code") setAutomationView({ kind: "library" });
    setActiveModule(module);
    setSidebarCollapsed(true);
  };

  const sendMessage = (prompt: string) => {
    const intent = detectIntent(prompt);
    const sourceReferences = sources
      .filter((source) =>
        activeConversation.selectedSourceIds.includes(source.id),
      )
      .map(({ id, name, origin }) => ({ id, name, origin }));
    updateActiveConversation((conversation) =>
      appendTurn(conversation, {
        id: `turn-${nextTurnId.current++}`,
        intent,
        locale,
        modelId: conversation.modelId,
        prompt,
        sourceReferences,
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
      testPlan: createLifeTestPlan(planId, null, "Athena"),
    });
    updateAutomationTurn(turn.id, {
      stage: "review-test-plan",
      testPlanId: planId,
      automationId: null,
      automationType: turn.automationWorkflow?.automationType ?? null,
    });
  };

  const createAthenaAutomation = (
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
        "Athena",
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
    createAthenaAutomation(turn, automationType);
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
    createAthenaAutomation(
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
    createAthenaAutomation(
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
      (plan) => plan.source === "Athena",
    );
    const planId = existing?.id ?? `TP-${nextPlanId.current++}`;
    if (existing === undefined) {
      dispatchArtifact({
        type: "test-plan/create",
        testPlan: createLifeTestPlan(planId, null, "Athena"),
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
    lastAthenaModule.current = "athena";
    setActiveModule("athena");
    setSidebarCollapsed(isNarrowViewport);
  };

  const addLocalSource = (source: Pick<LibrarySource, "name" | "type">) => {
    setLocalSources((current) => [
      ...current,
      {
        ...source,
        id: `local-source-${nextLocalSourceId.current++}`,
      },
    ]);
  };

  return (
    <div
      className={`tap-product-shell${athenaSidebarOpen ? " tap-product-shell--athena-open" : ""}`}
    >
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
        onSelectConversation={selectConversation}
        onToggleCollapsed={dismissAthenaSidebar}
      />
      {mobileAthenaDrawerOpen ? (
        <button
          type="button"
          className="tap-sidebar-scrim"
          aria-label={copy.navigation.closeSidebar}
          tabIndex={-1}
          onClick={dismissAthenaSidebar}
        />
      ) : null}
      <main
        className="tap-product-main"
        aria-hidden={mobileAthenaDrawerOpen ? true : undefined}
        inert={mobileAthenaDrawerOpen ? true : undefined}
      >
        {athenaWorkspaceActive && sidebarCollapsed ? (
          <button
            type="button"
            className="tap-panel-toggle tap-panel-toggle--floating tap-panel-toggle--left-expand"
            aria-controls="tap-athena-sidebar"
            aria-expanded="false"
            aria-label={copy.navigation.expandSidebar}
            onClick={expandAthenaSidebar}
          >
            <PanelToggleIcon side="left" state="collapsed" />
          </button>
        ) : null}
        <div hidden={activeModule !== "athena"}>
          <div
            className={`tap-athena-layout${sourcesCollapsed ? " tap-athena-layout--sources-collapsed" : ""}`}
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
            <AthenaChat
              agents={agents}
              conversation={activeConversation}
              copy={copy}
              isInert={compactSourcesDrawerOpen}
              onModelChange={(modelId: CodexModelId) =>
                updateActiveConversation((conversation) => ({
                  ...conversation,
                  modelId,
                }))
              }
              onSend={sendMessage}
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
              )}
              skills={skills}
              sources={sources}
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
              <KnowledgeSourcesPanel
                copy={copy}
                isLoading={documentsQuery.isPending}
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
    </div>
  );
}
