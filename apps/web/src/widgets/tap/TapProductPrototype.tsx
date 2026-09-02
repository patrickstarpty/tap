import {
  BookOutlined,
  CheckCircleFilled,
  CodeOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FileTextOutlined,
  MessageOutlined,
  PlusOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { Button, Checkbox, Drawer, Input, Select, Spin } from "antd";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { useDocumentListQuery } from "../../features/knowledge/api/queries";
import { KnowledgeLibrary } from "../../features/knowledge/components/KnowledgeLibrary";
import "./TapProductPrototype.css";

type ProductModule = "athena" | "test-management" | "low-code";
type AssistantIntent = "answer" | "test-plan" | "automation";
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

interface AssistantTurn {
  id: number;
  intent: AssistantIntent;
  prompt: string;
}

const MODULES: readonly {
  icon: typeof MessageOutlined;
  key: ProductModule;
  label: string;
}[] = [
  { key: "athena", label: "Athena", icon: MessageOutlined },
  {
    key: "test-management",
    label: "Test Management",
    icon: FileTextOutlined,
  },
  {
    key: "low-code",
    label: "Low Code Automation",
    icon: CodeOutlined,
  },
];

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

const QUICK_PROMPTS = [
  "Summarize the life insurance underwriting rules",
  "Create BDD test cases for life insurance underwriting",
  "Generate an automation script for a life insurance application",
] as const;

function detectIntent(prompt: string): AssistantIntent {
  const normalized = prompt.toLowerCase();
  const requestsCreation = [
    "生成",
    "创建",
    "编写",
    "设计",
    "构建",
    "产出",
    "generate",
    "create",
    "draft",
    "write",
    "design",
    "build",
    "produce",
    "automate",
  ].some((cue) => normalized.includes(cue));
  if (
    requestsCreation &&
    [
      "自动化",
      "脚本",
      "automation",
      "automate",
      "script",
      "playwright",
      "workflow",
    ].some((target) => normalized.includes(target))
  ) {
    return "automation";
  }
  if (
    requestsCreation &&
    [
      "测试用例",
      "测试计划",
      "测试场景",
      "bdd",
      "test case",
      "test plan",
      "test scenario",
    ].some((target) => normalized.includes(target))
  ) {
    return "test-plan";
  }
  return "answer";
}

function PrimaryNavigation({
  active,
  onChange,
}: {
  active: ProductModule;
  onChange: (module: ProductModule) => void;
}) {
  return (
    <aside className="tap-sidebar">
      <div className="tap-brand" aria-label="TAP">
        <span>T</span>
        <strong>TAP</strong>
      </div>
      <nav aria-label="Primary" className="tap-primary-navigation">
        {MODULES.map((module) => {
          const Icon = module.icon;
          return (
            <button
              key={module.key}
              type="button"
              className="tap-navigation-item"
              aria-label={module.label}
              aria-current={active === module.key ? "page" : undefined}
              onClick={() => onChange(module.key)}
            >
              <Icon aria-hidden="true" />
              <span>{module.label}</span>
            </button>
          );
        })}
      </nav>
      <div className="tap-sidebar-footer">
        <span className="tap-avatar">PT</span>
        <span>
          <strong>Prototype team</strong>
          <small>Local workspace</small>
        </span>
      </div>
    </aside>
  );
}

function KnowledgeSources({ onManage }: { onManage: () => void }) {
  const documentsQuery = useDocumentListQuery();
  const documents = documentsQuery.data?.items ?? [];
  const readyDocuments = documents.filter(
    (document) => document.status === "ready",
  );
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const initializedRef = useRef(false);

  useEffect(() => {
    if (initializedRef.current || readyDocuments.length === 0) return;
    initializedRef.current = true;
    setSelectedIds(
      new Set(readyDocuments.map((document) => document.documentId)),
    );
  }, [readyDocuments]);

  const toggle = (documentId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  };

  return (
    <aside className="tap-sources" aria-labelledby="tap-sources-heading">
      <header>
        <div>
          <h2 id="tap-sources-heading">Knowledge sources</h2>
          <p>Choose what Athena can use.</p>
        </div>
        <span className="tap-source-count">{selectedIds.size} selected</span>
      </header>

      {documentsQuery.isPending ? (
        <div
          className="tap-sources-loading"
          aria-label="Loading knowledge sources"
        >
          <Spin size="small" />
          <span>Loading sources</span>
        </div>
      ) : readyDocuments.length === 0 ? (
        <div className="tap-sources-empty">
          <BookOutlined aria-hidden="true" />
          <span>No ready sources</span>
        </div>
      ) : (
        <div className="tap-source-list">
          {readyDocuments.map((document) => (
            <Checkbox
              key={document.documentId}
              checked={selectedIds.has(document.documentId)}
              onChange={() => toggle(document.documentId)}
            >
              <span className="tap-source-name">
                <strong>{document.filename}</strong>
                <small>Ready · immutable revision</small>
              </span>
            </Checkbox>
          ))}
        </div>
      )}

      <Button
        block
        aria-label="Manage knowledge"
        icon={<BookOutlined aria-hidden="true" />}
        onClick={onManage}
      >
        Manage knowledge
      </Button>
      <p className="tap-source-footnote">
        Answers and generated assets show which selected sources they used.
      </p>
    </aside>
  );
}

function BddPreview() {
  return (
    <pre className="tap-bdd-preview">
      <code>
        <span>Feature: Life insurance application underwriting</span>
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
  intent,
  onImportPlan,
  onOpenAutomation,
}: {
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
            <strong>BDD test plan ready</strong>
            <span>3 scenarios · Draft</span>
          </div>
        </div>
        <BddPreview />
        <div className="tap-artifact-actions">
          <Button type="primary" onClick={onImportPlan}>
            Import to Test Plan
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
          <strong>Automation draft ready</strong>
          <span>BDD scenario + 6 automation steps</span>
        </div>
      </div>
      <BddPreview />
      <div className="tap-automation-summary">
        <span>Navigate</span>
        <span>Click</span>
        <span>Fill</span>
        <span>Assert</span>
      </div>
      <div className="tap-artifact-actions">
        <Button onClick={onImportPlan}>Import BDD as Test Plan</Button>
        <Button type="primary" onClick={onOpenAutomation}>
          Open in Low Code Automation
        </Button>
      </div>
    </article>
  );
}

function AthenaAssistant({
  onImportPlan,
  onOpenAutomation,
}: {
  onImportPlan: () => void;
  onOpenAutomation: () => void;
}) {
  const [message, setMessage] = useState("");
  const [turns, setTurns] = useState<readonly AssistantTurn[]>([]);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const nextTurnId = useRef(1);

  const submitMessage = (value: string) => {
    const prompt = value.trim();
    if (prompt.length === 0) return;
    setTurns((current) => [
      ...current,
      { id: nextTurnId.current++, prompt, intent: detectIntent(prompt) },
    ]);
    setMessage("");
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submitMessage(message);
  };

  const handleComposerKeyDown = (
    event: ReactKeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if (
      event.key !== "Enter" ||
      event.shiftKey ||
      event.nativeEvent.isComposing ||
      event.nativeEvent.keyCode === 229
    ) {
      return;
    }
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  };

  const hasTurns = turns.length > 0;
  const composer = (
    <form
      className="tap-composer"
      aria-label="Message composer"
      onSubmit={submit}
    >
      <label className="athena-visually-hidden" htmlFor="tap-message">
        Message Athena
      </label>
      <Input.TextArea
        id="tap-message"
        value={message}
        rows={3}
        placeholder="Ask Athena anything..."
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={handleComposerKeyDown}
      />
      <div className="tap-composer-footer">
        <span>Answers use your selected sources when available.</span>
        <Button
          type="primary"
          shape="circle"
          htmlType="submit"
          aria-label="Send"
          disabled={message.trim().length === 0}
          icon={<SendOutlined aria-hidden="true" />}
        />
      </div>
    </form>
  );

  return (
    <div className="tap-athena-layout">
      <section
        className={`tap-chat ${hasTurns ? "tap-chat--active" : "tap-chat--idle"}`}
        aria-label={hasTurns ? "Athena assistant" : "Start a conversation"}
      >
        {hasTurns ? (
          <div
            className="tap-chat-transcript"
            role="log"
            aria-label="Conversation"
            aria-live="polite"
          >
            <div className="tap-conversation">
              {turns.map((turn) => (
                <div className="tap-turn" key={turn.id}>
                  <div className="tap-user-message">{turn.prompt}</div>
                  <div className="tap-assistant-message">
                    <span className="tap-assistant-avatar">A</span>
                    <AssistantResponse
                      intent={turn.intent}
                      onImportPlan={onImportPlan}
                      onOpenAutomation={onOpenAutomation}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="tap-chat-welcome">
            <div className="tap-athena-mark" aria-hidden="true">
              A
            </div>
            <h1>What can I do for you?</h1>
            <p>
              Ask a question, create BDD test cases, or build an automation.
            </p>
          </div>
        )}
        {composer}
        {!hasTurns ? (
          <div className="tap-quick-prompts" aria-label="Suggested prompts">
            {QUICK_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => submitMessage(prompt)}
              >
                {prompt}
              </button>
            ))}
          </div>
        ) : null}
      </section>

      <KnowledgeSources onManage={() => setKnowledgeOpen(true)} />

      <Drawer
        title="Knowledge Library"
        size="large"
        open={knowledgeOpen}
        destroyOnHidden={false}
        onClose={() => setKnowledgeOpen(false)}
      >
        <KnowledgeLibrary />
      </Drawer>
    </div>
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

export function TapProductPrototype() {
  const [activeModule, setActiveModule] = useState<ProductModule>("athena");
  const [plans, setPlans] = useState<readonly TestPlan[]>(INITIAL_PLANS);
  const [automationSteps, setAutomationSteps] = useState<
    readonly AutomationStep[]
  >(INITIAL_AUTOMATION_STEPS);

  const importPlan = () => {
    setPlans((current) =>
      current.some((plan) => plan.id === GENERATED_PLAN.id)
        ? current
        : [GENERATED_PLAN, ...current],
    );
    setActiveModule("test-management");
  };

  return (
    <div className="tap-product-shell">
      <PrimaryNavigation active={activeModule} onChange={setActiveModule} />
      <main className="tap-product-main">
        <div hidden={activeModule !== "athena"}>
          <AthenaAssistant
            onImportPlan={importPlan}
            onOpenAutomation={() => setActiveModule("low-code")}
          />
        </div>
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
