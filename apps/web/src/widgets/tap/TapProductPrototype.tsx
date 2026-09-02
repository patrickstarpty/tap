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
        <p>
          根据当前选择的知识来源，寿险投保通常需要投保人和被保险人身份资料、健康告知、受益人信息以及可核验的缴费资料。
        </p>
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
        aria-label="Generated BDD test plan"
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
      aria-label="Generated automation"
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

function TestManagement({ plans }: { plans: readonly TestPlan[] }) {
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
          <h1 id="test-management-heading">Test Management</h1>
          <p>Plan coverage and reusable data in one workspace.</p>
        </div>
        <Button type="primary" icon={<PlusOutlined />}>
          New Test Plan
        </Button>
      </header>

      <div
        className="tap-section-tabs"
        role="tablist"
        aria-label="Test Management sections"
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
          Test Plan
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
          Test Data
        </button>
      </div>

      {section === "plans" ? (
        <div
          id="tap-test-plan-panel"
          className="tap-plan-workspace"
          role="tabpanel"
          aria-label="Test Plan"
          aria-labelledby="tap-test-plan-tab"
        >
          <div className="tap-list-toolbar">
            <span>{plans.length} test plans</span>
            <Input.Search
              aria-label="Search test plans"
              placeholder="Search plans"
            />
          </div>
          <div className="tap-plan-table" role="table" aria-label="Test plans">
            <div className="tap-plan-row tap-plan-row-header" role="row">
              <span role="columnheader">Name</span>
              <span role="columnheader">Scenarios</span>
              <span role="columnheader">Source</span>
              <span role="columnheader">Status</span>
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
                    ? "Imported from Athena"
                    : "Created manually"}
                </span>
                <span role="cell" className="tap-status-ready">
                  <CheckCircleFilled aria-hidden="true" /> Draft
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
          aria-label="Test Data"
          aria-labelledby="tap-test-data-tab"
        >
          <DatabaseOutlined aria-hidden="true" />
          <h2>Reusable test data</h2>
          <p>Test data sets will appear here.</p>
          <Button icon={<PlusOutlined />}>New data set</Button>
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
  steps,
  onStepsChange,
}: {
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
          <h1 id="low-code-heading">Life insurance application automation</h1>
          <p>Generated by Athena · Playwright · Draft</p>
        </div>
        <div className="tap-heading-actions">
          {saved ? <span className="tap-saved-state">Saved</span> : null}
          <Button type="primary" onClick={() => setSaved(true)}>
            Save draft
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
              <h2 id="automation-steps-heading">Automation steps</h2>
              <span>{steps.length} steps</span>
            </div>
            <Button
              aria-label="Add step"
              icon={<PlusOutlined aria-hidden="true" />}
              onClick={addStep}
            >
              Add step
            </Button>
          </div>
          <ol className="tap-step-list">
            {steps.map((step, index) => (
              <li key={step.id} aria-label={`Automation step ${index + 1}`}>
                <span className="tap-step-number">{index + 1}</span>
                <div className="tap-step-fields">
                  <label>
                    <span>Action</span>
                    <Select
                      aria-label={`Action for step ${index + 1}`}
                      value={step.action}
                      options={[
                        { value: "Navigate", label: "Navigate" },
                        { value: "Click", label: "Click" },
                        { value: "Fill", label: "Fill" },
                        { value: "Assert", label: "Assert" },
                        { value: "Wait", label: "Wait" },
                      ]}
                      onChange={(action: AutomationStep["action"]) =>
                        updateStep(step.id, { action })
                      }
                    />
                  </label>
                  <label>
                    <span>Element or URL</span>
                    <Input
                      aria-label={`Element for step ${index + 1}`}
                      value={step.target}
                      placeholder="CSS selector, text, or URL"
                      onChange={(event) =>
                        updateStep(step.id, { target: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    <span>Value</span>
                    <Input
                      aria-label={`Value for step ${index + 1}`}
                      value={step.value}
                      placeholder="Optional"
                      onChange={(event) =>
                        updateStep(step.id, { value: event.target.value })
                      }
                    />
                  </label>
                </div>
                <Button
                  type="text"
                  danger
                  aria-label={`Delete step ${index + 1}`}
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
              <h2 id="script-preview-heading">Generated script</h2>
              <span>Updates with every step</span>
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [conversations, setConversations] = useState<readonly Conversation[]>(
    () => [createConversation("chat-1")],
  );
  const [activeConversationId, setActiveConversationId] = useState("chat-1");
  const [plans, setPlans] = useState<readonly TestPlan[]>(INITIAL_PLANS);
  const [agents, setAgents] = useState<readonly CatalogItem[]>(BUILT_IN_AGENTS);
  const [skills, setSkills] = useState<readonly CatalogItem[]>(BUILT_IN_SKILLS);
  const [localSources, setLocalSources] = useState<readonly LibrarySource[]>(
    [],
  );
  const [automationSteps, setAutomationSteps] = useState<
    readonly AutomationStep[]
  >(INITIAL_AUTOMATION_STEPS);
  const nextConversationId = useRef(2);
  const nextTurnId = useRef(1);
  const nextCatalogId = useRef(1);
  const nextLocalSourceId = useRef(1);

  const copy = PROTOTYPE_COPY[locale];
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
        description: `Knowledge source · ${document.stage}`,
      })),
    [documentsQuery.data?.items],
  );
  const sources = useMemo<readonly LibrarySource[]>(
    () => [...documentSources, ...localSources],
    [documentSources, localSources],
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
  };

  const selectConversation = (conversationId: string) => {
    setActiveConversationId(conversationId);
    setActiveModule("athena");
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
        status: "ready",
        description: "Local source · page-only",
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
        onModuleChange={setActiveModule}
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
          <TestManagement plans={plans} />
        ) : null}
        {activeModule === "low-code" ? (
          <LowCodeAutomation
            steps={automationSteps}
            onStepsChange={setAutomationSteps}
          />
        ) : null}
      </main>
    </div>
  );
}
