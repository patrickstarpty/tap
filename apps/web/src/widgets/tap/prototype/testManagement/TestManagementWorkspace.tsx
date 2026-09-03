import {
  ArrowLeftOutlined,
  CheckCircleFilled,
  CodeOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  LinkOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import {
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import type { Locale } from "../model";
import type {
  ArtifactState,
  Automation,
  AutomationRun,
  ExecutionTarget,
} from "../artifacts/model";
import {
  selectAvailableAutomations,
  selectTestPlanRuns,
} from "../artifacts/state";
import { AutomationRunPanel } from "../automation/AutomationWorkspace";

type TestManagementSection = "plans" | "data";

const TEXT = {
  en: {
    heading: "Test Management",
    description:
      "Plan coverage, linked automation, and execution records in one workspace.",
    newTestPlan: "New Test Plan",
    testPlan: "Test Plan",
    testData: "Test Data",
    search: "Search plans",
    listLabel: "Test plan list",
    count: "test plans",
    name: "Name",
    scenarios: "Scenarios",
    automation: "Automation",
    source: "Source",
    status: "Status",
    imported: "Imported from Athena",
    manual: "Created manually",
    ready: "Ready",
    draft: "Draft",
    open: "Open",
    sections: "Test Management sections",
    dataHeading: "Reusable test data",
    dataEmpty: "Test data sets will appear here.",
    newData: "New data set",
    back: "Back to Test Plans",
    linkedAutomation: "Linked Automation",
    noAutomation: "No Automation linked",
    linkAutomation: "Link Automation",
    unlinkAutomation: "Unlink Automation",
    openAutomation: "Open Automation",
    executionHistory: "Test Plan execution history",
    noExecutions: "No automated executions yet",
    linkToRun: "Link an Automation to run this plan",
    scenarioCoverage: "Scenario coverage",
    mapped: "Mapped",
  },
  zh: {
    heading: "测试管理",
    description: "在同一工作区查看覆盖、关联自动化和执行记录。",
    newTestPlan: "新建测试计划",
    testPlan: "测试计划",
    testData: "测试数据",
    search: "搜索测试计划",
    listLabel: "测试计划列表",
    count: "个测试计划",
    name: "名称",
    scenarios: "场景",
    automation: "自动化",
    source: "来源",
    status: "状态",
    imported: "从 Athena 导入",
    manual: "手动创建",
    ready: "就绪",
    draft: "草稿",
    open: "打开",
    sections: "测试管理分区",
    dataHeading: "可复用测试数据",
    dataEmpty: "测试数据集将显示在这里。",
    newData: "新建数据集",
    back: "返回测试计划",
    linkedAutomation: "已关联自动化",
    noAutomation: "未关联自动化",
    linkAutomation: "关联自动化",
    unlinkAutomation: "解除自动化关联",
    openAutomation: "打开自动化",
    executionHistory: "测试计划执行记录",
    noExecutions: "还没有自动化执行记录",
    linkToRun: "关联一个自动化后即可执行此测试计划",
    scenarioCoverage: "场景覆盖",
    mapped: "已映射",
  },
} as const;

function TestPlanDetail({
  state,
  planId,
  locale,
  onBack,
  onOpenAutomation,
  onLink,
  onRun,
}: {
  state: ArtifactState;
  planId: string;
  locale: Locale;
  onBack: () => void;
  onOpenAutomation: (automationId: string) => void;
  onLink: (automationId: string, testPlanId: string | null) => void;
  onRun: (
    automation: Automation,
    target: ExecutionTarget,
    triggeredFrom: AutomationRun["triggeredFrom"],
  ) => void;
}) {
  const text = TEXT[locale];
  const plan = state.testPlans.find(({ id }) => id === planId);
  const [automationChoice, setAutomationChoice] = useState("");
  if (plan === undefined) {
    return (
      <section className="tap-module tap-not-found">
        <h1>Test Plan not found</h1>
        <Button onClick={onBack}>{text.back}</Button>
      </section>
    );
  }
  const automation = state.automations.find(
    ({ id }) => id === plan.automationId,
  );
  const availableAutomations = selectAvailableAutomations(state, plan.id);
  const runs = selectTestPlanRuns(state, plan.id);

  return (
    <section
      className="tap-module tap-test-plan-detail"
      aria-labelledby="test-plan-detail-heading"
    >
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>
        {text.back}
      </Button>
      <header className="tap-test-plan-detail-header">
        <div>
          <span className="tap-asset-id">{plan.id}</span>
          <h1 id="test-plan-detail-heading">{plan.title}</h1>
          <p>
            {plan.source === "Athena" ? text.imported : text.manual} ·{" "}
            {plan.status === "ready" ? text.ready : text.draft}
          </p>
        </div>
        <span className="tap-type-badge">
          {plan.scenarios.length} {text.scenarios}
        </span>
      </header>

      <div className="tap-test-plan-layout">
        <div className="tap-test-plan-main">
          <section
            className="tap-related-asset"
            aria-label={text.linkedAutomation}
          >
            <div className="tap-panel-heading">
              <h2>{text.linkedAutomation}</h2>
              <LinkOutlined aria-hidden="true" />
            </div>
            {automation === undefined ? (
              <div className="tap-link-empty">
                <p>{text.noAutomation}</p>
                <select
                  aria-label={text.automation}
                  value={automationChoice}
                  onChange={(event) => setAutomationChoice(event.target.value)}
                >
                  <option value="">{text.noAutomation}</option>
                  {availableAutomations.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id} · {item.title}
                    </option>
                  ))}
                </select>
                <Button
                  disabled={automationChoice === ""}
                  onClick={() => onLink(automationChoice, plan.id)}
                >
                  {text.linkAutomation}
                </Button>
              </div>
            ) : (
              <div className="tap-linked-asset-row">
                <CodeOutlined aria-hidden="true" />
                <span>
                  <strong>{automation.title}</strong>
                  <small>
                    {automation.id} ·{" "}
                    {automation.type === "web" ? "Web" : "Mobile"}
                  </small>
                </span>
                <Button onClick={() => onOpenAutomation(automation.id)}>
                  {text.openAutomation} {automation.id}
                </Button>
                <Button type="text" onClick={() => onLink(automation.id, null)}>
                  {text.unlinkAutomation}
                </Button>
              </div>
            )}
          </section>

          <section
            className="tap-plan-scenarios"
            aria-labelledby="plan-scenarios-heading"
          >
            <div className="tap-panel-heading">
              <h2 id="plan-scenarios-heading">{text.scenarioCoverage}</h2>
              <span>{plan.scenarios.length}</span>
            </div>
            <ol>
              {plan.scenarios.map((scenario, scenarioIndex) => (
                <li key={scenario.id}>
                  <div>
                    <small>{String(scenarioIndex + 1).padStart(2, "0")}</small>
                    <strong>{scenario.title}</strong>
                    {automation === undefined ? null : (
                      <span>
                        {text.mapped} · {automation.id}
                      </span>
                    )}
                  </div>
                  <ol>
                    {scenario.steps.map((step) => (
                      <li key={step.id}>
                        <span className="tap-bdd-keyword">{step.keyword}</span>
                        <span>{step.text}</span>
                        <small>{step.id}</small>
                      </li>
                    ))}
                  </ol>
                </li>
              ))}
            </ol>
          </section>
        </div>

        <aside className="tap-test-plan-execution">
          {automation === undefined ? (
            <>
              <div className="tap-run-gate">
                <LinkOutlined aria-hidden="true" />
                <strong>{text.linkToRun}</strong>
              </div>
              <section
                className="tap-run-history"
                aria-label={text.executionHistory}
              >
                <div className="tap-panel-heading">
                  <h3>{text.executionHistory}</h3>
                  <span>0</span>
                </div>
                <p className="tap-empty-note">{text.noExecutions}</p>
              </section>
            </>
          ) : (
            <AutomationRunPanel
              automation={automation}
              runs={runs}
              locale={locale}
              triggeredFrom="test-plan"
              historyLabel={text.executionHistory}
              onRun={onRun}
            />
          )}
        </aside>
      </div>
    </section>
  );
}

export function TestManagementWorkspace({
  state,
  selectedPlanId,
  locale,
  onOpenPlan,
  onBack,
  onOpenAutomation,
  onLink,
  onRun,
}: {
  state: ArtifactState;
  selectedPlanId: string | null;
  locale: Locale;
  onOpenPlan: (planId: string) => void;
  onBack: () => void;
  onOpenAutomation: (automationId: string) => void;
  onLink: (automationId: string, testPlanId: string | null) => void;
  onRun: (
    automation: Automation,
    target: ExecutionTarget,
    triggeredFrom: AutomationRun["triggeredFrom"],
  ) => void;
}) {
  const text = TEXT[locale];
  const [section, setSection] = useState<TestManagementSection>("plans");
  const [query, setQuery] = useState("");
  const planTabRef = useRef<HTMLButtonElement>(null);
  const dataTabRef = useRef<HTMLButtonElement>(null);

  if (selectedPlanId !== null) {
    return (
      <TestPlanDetail
        state={state}
        planId={selectedPlanId}
        locale={locale}
        onBack={onBack}
        onOpenAutomation={onOpenAutomation}
        onLink={onLink}
        onRun={onRun}
      />
    );
  }

  const handleTabKeyDown = (
    event: ReactKeyboardEvent<HTMLButtonElement>,
    current: TestManagementSection,
  ) => {
    let next: TestManagementSection | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      next = current === "plans" ? "data" : "plans";
    } else if (event.key === "Home") next = "plans";
    else if (event.key === "End") next = "data";
    if (next === null) return;
    event.preventDefault();
    setSection(next);
    (next === "plans" ? planTabRef : dataTabRef).current?.focus();
  };

  const plans = state.testPlans.filter((plan) =>
    plan.title.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <section
      className="tap-module tap-test-management"
      aria-labelledby="test-management-heading"
    >
      <header className="tap-module-heading">
        <div>
          <h1 id="test-management-heading">{text.heading}</h1>
          <p>{text.description}</p>
        </div>
        <Button
          aria-label={text.newTestPlan}
          type="primary"
          icon={<PlusOutlined />}
        >
          {text.newTestPlan}
        </Button>
      </header>
      <div
        className="tap-section-tabs"
        role="tablist"
        aria-label={text.sections}
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
          {text.testPlan}
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
          {text.testData}
        </button>
      </div>
      {section === "plans" ? (
        <div
          id="tap-test-plan-panel"
          className="tap-plan-workspace"
          role="tabpanel"
          aria-label={text.testPlan}
          aria-labelledby="tap-test-plan-tab"
        >
          <div className="tap-list-toolbar">
            <span>
              {state.testPlans.length} {text.count}
            </span>
            <Input.Search
              aria-label={text.search}
              placeholder={text.search}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div
            className="tap-plan-table"
            role="table"
            aria-label={text.listLabel}
          >
            <div
              className="tap-plan-row tap-plan-row-header tap-plan-row--automation"
              role="row"
            >
              <span role="columnheader">{text.name}</span>
              <span role="columnheader">{text.scenarios}</span>
              <span role="columnheader">{text.automation}</span>
              <span role="columnheader">{text.source}</span>
              <span role="columnheader">{text.status}</span>
              <span role="columnheader" aria-label={text.open} />
            </div>
            {plans.map((plan) => (
              <div
                className="tap-plan-row tap-plan-row--automation"
                role="row"
                key={plan.id}
              >
                <span role="cell">
                  <FileTextOutlined aria-hidden="true" />
                  <span>
                    <strong>{plan.title}</strong>
                    <small>{plan.id}</small>
                  </span>
                </span>
                <span role="cell">{plan.scenarios.length}</span>
                <span role="cell">
                  {plan.automationId ?? text.noAutomation}
                </span>
                <span role="cell">
                  {plan.source === "Athena" ? text.imported : text.manual}
                </span>
                <span role="cell" className="tap-status-ready">
                  <CheckCircleFilled aria-hidden="true" />{" "}
                  {plan.status === "ready" ? text.ready : text.draft}
                </span>
                <span role="cell">
                  <Button onClick={() => onOpenPlan(plan.id)}>
                    {text.open} {plan.id}
                  </Button>
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
          aria-label={text.testData}
          aria-labelledby="tap-test-data-tab"
        >
          <DatabaseOutlined aria-hidden="true" />
          <h2>{text.dataHeading}</h2>
          <p>{text.dataEmpty}</p>
          <Button icon={<PlusOutlined />}>{text.newData}</Button>
        </div>
      )}
    </section>
  );
}
