import {
  CheckCircleFilled,
  CodeOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FileTextOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input, Select } from "antd";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { useDocumentListQuery } from "../../features/knowledge/api/queries";
import { AthenaChat } from "./prototype/AthenaChat";
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
  detectIntent,
  type AssistantIntent,
  type CatalogItem,
  type Conversation,
  type LibrarySource,
  type Locale,
  type ProductModule,
} from "./prototype/model";
import { PrototypeSidebar } from "./prototype/PrototypeSidebar";
import "./TapProductPrototype.css";

type TestManagementSection = "plans" | "data";

interface TestPlan {
  id: string;
  title: string;
  scenarios: number;
  source: "Manual" | "Athena";
}

interface AutomationStep {
  action: "Navigate" | "Click" | "Fill" | "Assert" | "Wait";
  id: string;
  target: string;
  value: string;
}

const INITIAL_PLANS: readonly TestPlan[] = [
  {
    id: "plan-life-application",
    title: "Life policy application regression",
    scenarios: 14,
    source: "Manual",
  },
  {
    id: "plan-beneficiary",
    title: "Beneficiary maintenance",
    scenarios: 9,
    source: "Manual",
  },
];

const GENERATED_PLAN: TestPlan = {
  id: "plan-life-underwriting",
  title: "Life insurance application underwriting",
  scenarios: 3,
  source: "Athena",
};

const INITIAL_AUTOMATION_STEPS: readonly AutomationStep[] = [
  {
    id: "step-1",
    action: "Navigate",
    target: "/life/applications/new",
    value: "",
  },
  {
    id: "step-2",
    action: "Click",
    target: "button[data-testid='start-application']",
    value: "",
  },
  {
    id: "step-3",
    action: "Fill",
    target: "input[name='sumAssured']",
    value: "1000000",
  },
  {
    id: "step-4",
    action: "Fill",
    target: "textarea[name='healthDeclaration']",
    value: "No disclosed conditions",
  },
  {
    id: "step-5",
    action: "Click",
    target: "button[data-testid='submit-underwriting']",
    value: "",
  },
  {
    id: "step-6",
    action: "Assert",
    target: "[data-testid='underwriting-status']",
    value: "Pending underwriting",
  },
];

function BddPreview({ copy }: { copy: PrototypeCopy }) {
  return (
    <pre className="tap-bdd-preview">
      <code>
        <span>{copy.artifacts.feature}</span>
        {"\n\n"}
        <span> Scenario: Complete application enters underwriting</span>
        {"\n"}
        {
          "   Given an adult applicant with completed identity and health declarations\n"
        }
        {"   When the applicant submits a complete term life application\n"}
        {'   Then the application status should be "Pending underwriting"\n\n'}
        <span> Scenario: Missing health disclosure is blocked</span>
        {"\n"}
        {"   Given mandatory health disclosure answers are missing\n"}
        {"   When the applicant submits the life insurance application\n"}
        {"   Then the application should show a validation error\n\n"}
        <span> Scenario: High coverage requires manual review</span>
        {"\n"}
        {
          "   Given the requested sum assured exceeds the straight-through limit\n"
        }
        {"   When the applicant submits the life insurance application\n"}
        {
          '   Then the application status should be "Additional review required"'
        }
      </code>
    </pre>
  );
}

function AssistantResponse({
  copy,
  intent,
  onImportPlan,
  onOpenAutomation,
}: {
  copy: PrototypeCopy;
  intent: AssistantIntent;
  onImportPlan: () => void;
  onOpenAutomation: () => void;
}) {
  if (intent === "answer") {
    return (
      <div className="tap-answer-copy">
        <p>{copy.chat.answer}</p>
        <span className="tap-citation-reference">
          [1] life-underwriting-rules.md
        </span>
      </div>
    );
  }

  if (intent === "test-plan") {
    return (
      <article
        className="tap-generated-artifact"
        aria-label={copy.artifacts.bddPlanLabel}
      >
        <div className="tap-artifact-heading">
          <span className="tap-artifact-icon">
            <FileTextOutlined aria-hidden="true" />
          </span>
          <div>
            <strong>{copy.artifacts.bddPlanReady}</strong>
            <span>{copy.artifacts.scenariosDraft}</span>
          </div>
        </div>
        <BddPreview copy={copy} />
        <div className="tap-artifact-actions">
          <Button type="primary" onClick={onImportPlan}>
            {copy.testManagement.importToTestPlan}
          </Button>
        </div>
      </article>
    );
  }

  return (
    <article
      className="tap-generated-artifact"
      aria-label={copy.artifacts.automationLabel}
    >
      <div className="tap-artifact-heading">
        <span className="tap-artifact-icon">
          <CodeOutlined aria-hidden="true" />
        </span>
        <div>
          <strong>{copy.artifacts.automationReady}</strong>
          <span>{copy.artifacts.automationSummary}</span>
        </div>
      </div>
      <BddPreview copy={copy} />
      <div className="tap-automation-summary">
        <span>Navigate</span>
        <span>Click</span>
        <span>Fill</span>
        <span>Assert</span>
      </div>
      <div className="tap-artifact-actions">
        <Button onClick={onImportPlan}>
          {copy.testManagement.importBddAsTestPlan}
        </Button>
        <Button type="primary" onClick={onOpenAutomation}>
          {copy.lowCode.openInLowCode}
        </Button>
      </div>
    </article>
  );
}

function TestManagement({
  copy,
  plans,
}: {
  copy: PrototypeCopy;
  plans: readonly TestPlan[];
}) {
  const [section, setSection] = useState<TestManagementSection>("plans");
  const planTabRef = useRef<HTMLButtonElement>(null);
  const dataTabRef = useRef<HTMLButtonElement>(null);

  const handleTabKeyDown = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    current: TestManagementSection,
  ) => {
    let next: TestManagementSection | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      next = current === "plans" ? "data" : "plans";
    } else if (event.key === "Home") {
      next = "plans";
    } else if (event.key === "End") {
      next = "data";
    }
    if (next === null) return;
    event.preventDefault();
    setSection(next);
    (next === "plans" ? planTabRef : dataTabRef).current?.focus();
  };

  return (
    <section className="tap-module" aria-labelledby="test-management-heading">
      <header className="tap-module-heading">
        <div>
          <h1 id="test-management-heading">{copy.testManagement.heading}</h1>
          <p>{copy.testManagement.description}</p>
        </div>
        <Button type="primary" icon={<PlusOutlined />}>
          {copy.testManagement.newTestPlan}
        </Button>
      </header>

      <div
        className="tap-section-tabs"
        role="tablist"
        aria-label={copy.testManagement.sections}
      >
        <button
          ref={planTabRef}
          id="tap-test-plan-tab"
          type="button"
          role="tab"
          aria-selected={section === "plans"}
          aria-controls="tap-test-plan-panel"
          tabIndex={section === "plans" ? 0 : -1}
          onClick={() => setSection("plans")}
          onKeyDown={(event) => handleTabKeyDown(event, "plans")}
        >
          {copy.testManagement.testPlan}
        </button>
        <button
          ref={dataTabRef}
          id="tap-test-data-tab"
          type="button"
          role="tab"
          aria-selected={section === "data"}
          aria-controls="tap-test-data-panel"
          tabIndex={section === "data" ? 0 : -1}
          onClick={() => setSection("data")}
          onKeyDown={(event) => handleTabKeyDown(event, "data")}
        >
          {copy.testManagement.testData}
        </button>
      </div>

      {section === "plans" ? (
        <div
          id="tap-test-plan-panel"
          className="tap-plan-workspace"
          role="tabpanel"
          aria-label={copy.testManagement.testPlan}
          aria-labelledby="tap-test-plan-tab"
        >
          <div className="tap-list-toolbar">
            <span>
              {plans.length} {copy.testManagement.testPlans}
            </span>
            <Input.Search
              aria-label={copy.testManagement.searchPlans}
              placeholder={copy.testManagement.searchPlans}
            />
          </div>
          <div
            className="tap-plan-table"
            role="table"
            aria-label={copy.testManagement.testPlans}
          >
            <div className="tap-plan-row tap-plan-row-header" role="row">
              <span role="columnheader">{copy.testManagement.nameColumn}</span>
              <span role="columnheader">
                {copy.testManagement.scenariosColumn}
              </span>
              <span role="columnheader">
                {copy.testManagement.sourceColumn}
              </span>
              <span role="columnheader">
                {copy.testManagement.statusColumn}
              </span>
            </div>
            {plans.map((plan) => (
              <div className="tap-plan-row" role="row" key={plan.id}>
                <span role="cell">
                  <FileTextOutlined aria-hidden="true" />
                  <strong>{plan.title}</strong>
                </span>
                <span role="cell">{plan.scenarios}</span>
                <span role="cell">
                  {plan.source === "Athena"
                    ? copy.testManagement.importedFromAthena
                    : copy.testManagement.createdManually}
                </span>
                <span role="cell" className="tap-status-ready">
                  <CheckCircleFilled aria-hidden="true" />{" "}
                  {copy.testManagement.draft}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div
          id="tap-test-data-panel"
          className="tap-data-workspace"
          role="tabpanel"
          aria-label={copy.testManagement.testData}
          aria-labelledby="tap-test-data-tab"
        >
          <DatabaseOutlined aria-hidden="true" />
          <h2>{copy.testManagement.reusableTestData}</h2>
          <p>{copy.testManagement.testDataEmpty}</p>
          <Button icon={<PlusOutlined />}>
            {copy.testManagement.newDataSet}
          </Button>
        </div>
      )}
    </section>
  );
}

function scriptForStep(step: AutomationStep): string {
  const target = JSON.stringify(step.target);
  const value = JSON.stringify(step.value);
  if (step.action === "Navigate") return `await page.goto(${target});`;
  if (step.action === "Click") return `await page.locator(${target}).click();`;
  if (step.action === "Fill")
    return `await page.locator(${target}).fill(${value});`;
  if (step.action === "Assert") {
    return `await expect(page.locator(${target})).toContainText(${value});`;
  }
  return `await page.locator(${target}).waitFor();`;
}

function LowCodeAutomation({
  copy,
  steps,
  onStepsChange,
}: {
  copy: PrototypeCopy;
  steps: readonly AutomationStep[];
  onStepsChange: (steps: readonly AutomationStep[]) => void;
}) {
  const [saved, setSaved] = useState(false);
  const code = useMemo(
    () => steps.map((step) => `  ${scriptForStep(step)}`).join("\n"),
    [steps],
  );

  const updateStep = (id: string, patch: Partial<AutomationStep>) => {
    setSaved(false);
    onStepsChange(
      steps.map((step) => (step.id === id ? { ...step, ...patch } : step)),
    );
  };

  const addStep = () => {
    const number =
      steps.reduce((maximum, step) => {
        const match = /^step-(\d+)$/.exec(step.id);
        return match === null ? maximum : Math.max(maximum, Number(match[1]));
      }, 0) + 1;
    setSaved(false);
    onStepsChange([
      ...steps,
      {
        id: `step-${number}`,
        action: "Click",
        target: "",
        value: "",
      },
    ]);
  };

  const deleteStep = (id: string) => {
    setSaved(false);
    onStepsChange(steps.filter((step) => step.id !== id));
  };

  return (
    <section
      className="tap-module tap-low-code"
      aria-labelledby="low-code-heading"
    >
      <header className="tap-module-heading">
        <div>
          <h1 id="low-code-heading">{copy.lowCode.heading}</h1>
          <p>{copy.lowCode.description}</p>
        </div>
        <div className="tap-heading-actions">
          {saved ? (
            <span className="tap-saved-state">{copy.lowCode.saved}</span>
          ) : null}
          <Button type="primary" onClick={() => setSaved(true)}>
            {copy.lowCode.saveDraft}
          </Button>
        </div>
      </header>

      <div className="tap-low-code-layout">
        <section
          className="tap-step-editor"
          aria-labelledby="automation-steps-heading"
        >
          <div className="tap-step-editor-heading">
            <div>
              <h2 id="automation-steps-heading">
                {copy.lowCode.automationSteps}
              </h2>
              <span>
                {steps.length} {copy.lowCode.steps}
              </span>
            </div>
            <Button
              aria-label={copy.lowCode.addStep}
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={addStep}
            >
              {copy.lowCode.addStep}
            </Button>
          </div>
          <ol className="tap-step-list">
            {steps.map((step, index) => (
              <li
                key={step.id}
                aria-label={`${copy.lowCode.automationStep} ${index + 1}`}
              >
                <span className="tap-step-number">{index + 1}</span>
                <div className="tap-step-fields">
                  <label>
                    <span>{copy.lowCode.action}</span>
                    <Select
                      aria-label={`${copy.lowCode.actionForStep} ${index + 1}`}
                      value={step.action}
                      options={[
                        { value: "Navigate", label: copy.lowCode.navigate },
                        { value: "Click", label: copy.lowCode.click },
                        { value: "Fill", label: copy.lowCode.fill },
                        { value: "Assert", label: copy.lowCode.assert },
                        { value: "Wait", label: copy.lowCode.wait },
                      ]}
                      onChange={(action: AutomationStep["action"]) =>
                        updateStep(step.id, { action })
                      }
                    />
                  </label>
                  <label>
                    <span>{copy.lowCode.elementOrUrl}</span>
                    <Input
                      aria-label={`${copy.lowCode.elementForStep} ${index + 1}`}
                      value={step.target}
                      placeholder={copy.lowCode.elementPlaceholder}
                      onChange={(event) =>
                        updateStep(step.id, { target: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    <span>{copy.lowCode.value}</span>
                    <Input
                      aria-label={`${copy.lowCode.valueForStep} ${index + 1}`}
                      value={step.value}
                      placeholder={copy.lowCode.optional}
                      onChange={(event) =>
                        updateStep(step.id, { value: event.target.value })
                      }
                    />
                  </label>
                </div>
                <Button
                  type="text"
                  danger
                  aria-label={`${copy.lowCode.deleteStep} ${index + 1}`}
                  icon={<DeleteOutlined aria-hidden="true" />}
                  onClick={() => deleteStep(step.id)}
                />
              </li>
            ))}
          </ol>
        </section>

        <aside
          className="tap-code-preview"
          aria-labelledby="script-preview-heading"
        >
          <header>
            <div>
              <h2 id="script-preview-heading">
                {copy.lowCode.generatedScript}
              </h2>
              <span>{copy.lowCode.updatesWithEveryStep}</span>
            </div>
            <span className="tap-file-name">
              life-policy-application.spec.ts
            </span>
          </header>
          <pre>
            <code>{`test("applicant submits a life insurance application", async ({ page }) => {\n${code}\n});`}</code>
          </pre>
        </aside>
      </div>
    </section>
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

export function TapProductPrototype() {
  const documentsQuery = useDocumentListQuery();
  const [locale, setLocale] = useState<Locale>("en");
  const [activeModule, setActiveModule] = useState<ProductModule>("athena");
  const [isNarrowViewport, setIsNarrowViewport] = useState(
    () => window.matchMedia("(max-width: 640px)").matches,
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.matchMedia("(max-width: 640px)").matches,
  );
  const [conversations, setConversations] = useState<readonly Conversation[]>(
    () => [createConversation("chat-1")],
  );
  const [activeConversationId, setActiveConversationId] = useState("chat-1");
  const [plans, setPlans] = useState<readonly TestPlan[]>(INITIAL_PLANS);
  const [agents, setAgents] = useState<readonly CatalogItem[]>(BUILT_IN_AGENTS);
  const [skills, setSkills] = useState<readonly CatalogItem[]>(BUILT_IN_SKILLS);
  const [localSources, setLocalSources] = useState<
    readonly Pick<LibrarySource, "id" | "name" | "type">[]
  >([]);
  const [automationSteps, setAutomationSteps] = useState<
    readonly AutomationStep[]
  >(INITIAL_AUTOMATION_STEPS);
  const nextConversationId = useRef(2);
  const nextTurnId = useRef(1);
  const nextCatalogId = useRef(1);
  const nextLocalSourceId = useRef(1);

  const copy = PROTOTYPE_COPY[locale];

  useEffect(() => {
    const media = window.matchMedia("(max-width: 640px)");
    const handleChange = (event: MediaQueryListEvent) => {
      setIsNarrowViewport(event.matches);
      if (event.matches) setSidebarCollapsed(true);
    };
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, []);

  const documentSources = useMemo<readonly LibrarySource[]>(
    () =>
      (documentsQuery.data?.items ?? []).map((document) => ({
        id: document.documentId,
        name: document.filename,
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
    setConversations((current) => [...current, createConversation(id)]);
    setActiveConversationId(id);
    setActiveModule("athena");
    if (isNarrowViewport) setSidebarCollapsed(true);
  };

  const selectConversation = (conversationId: string) => {
    setActiveConversationId(conversationId);
    setActiveModule("athena");
    if (isNarrowViewport) setSidebarCollapsed(true);
  };

  const selectModule = (module: ProductModule) => {
    setActiveModule(module);
    if (isNarrowViewport) setSidebarCollapsed(true);
  };

  const sendMessage = (prompt: string) => {
    updateActiveConversation((conversation) =>
      appendTurn(conversation, {
        id: `turn-${nextTurnId.current++}`,
        intent: detectIntent(prompt),
        prompt,
      }),
    );
  };

  const importPlan = () => {
    setPlans((current) =>
      current.some((plan) => plan.id === GENERATED_PLAN.id)
        ? current
        : [GENERATED_PLAN, ...current],
    );
    setActiveModule("test-management");
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
    updateActiveConversation((conversation) => {
      const selectedKey =
        kind === "agent" ? "selectedAgentIds" : "selectedSkillIds";
      const selectedIds = conversation[selectedKey];
      return selectedIds.includes(itemId)
        ? conversation
        : { ...conversation, [selectedKey]: [...selectedIds, itemId] };
    });
    setActiveModule("athena");
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
      className={`tap-product-shell${sidebarCollapsed ? " tap-product-shell--collapsed" : ""}`}
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
        onToggleCollapsed={() => setSidebarCollapsed((current) => !current)}
      />
      <main className="tap-product-main">
        <div hidden={activeModule !== "athena"}>
          <div className="tap-athena-layout">
            <AthenaChat
              agents={agents}
              conversation={activeConversation}
              copy={copy}
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
                  copy={copy}
                  intent={turn.intent}
                  onImportPlan={importPlan}
                  onOpenAutomation={() => setActiveModule("low-code")}
                />
              )}
              skills={skills}
              sources={sources}
            />
            <KnowledgeSourcesPanel
              copy={copy}
              isLoading={documentsQuery.isPending}
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
          <TestManagement copy={copy} plans={plans} />
        ) : null}
        {activeModule === "low-code" ? (
          <LowCodeAutomation
            copy={copy}
            steps={automationSteps}
            onStepsChange={setAutomationSteps}
          />
        ) : null}
      </main>
    </div>
  );
}
