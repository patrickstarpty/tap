import {
  ArrowLeftOutlined,
  CodeOutlined,
  LinkOutlined,
  PlusOutlined,
  RobotOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import { useMemo, useState } from "react";

import type { Locale } from "../model";
import { executionAgents, mobileDevices } from "../artifacts/fixtures";
import type {
  ArtifactState,
  Automation,
  AutomationRun,
  AutomationType,
  BddKeyword,
  ExecutionTarget,
  ImplementationAction,
  ImplementationActionType,
  MobilePlatform,
} from "../artifacts/model";
import {
  selectAutomationRuns,
  selectAvailableTestPlans,
} from "../artifacts/state";

export type AutomationWorkspaceView =
  | { kind: "library" }
  | { kind: "new" }
  | { kind: "detail"; automationId: string };

const TEXT = {
  en: {
    heading: "Low Code Automation",
    description:
      "Design business-readable BDD and bind every step to executable actions.",
    count: "automations",
    newAutomation: "New automation",
    search: "Search automations",
    name: "Name",
    type: "Type",
    testPlan: "Test Plan",
    scenarios: "Scenarios",
    lastRun: "Last run",
    never: "Never",
    open: "Open",
    back: "Back to Automation Library",
    draft: "Draft",
    ready: "Ready",
    linked: "Linked",
    unlinked: "Not linked",
    automationActions: "Automation actions",
    editActions: "Edit automation actions",
    doneEditing: "Done editing actions",
    addAction: "Add action",
    action: "Action",
    target: "Locator or target",
    value: "Value",
    mappedFrom: "Mapped from",
    noActions: "No implementation actions yet",
    addStep: "Add BDD step",
    save: "Save draft",
    saved: "Saved locally",
    generatedScript: "Generated script",
    run: "Run",
    aiAgent: "AI Agent",
    agentIntro:
      "Discuss changes to this Automation. Suggestions never overwrite BDD until you apply them.",
    messageAgent: "Message Automation AI Agent",
    propose: "Propose changes",
    proposal:
      "Suggested change: add a validation scenario for missing health disclosures.",
    apply: "Apply suggestion",
    reject: "Reject",
    applied: "Suggestion applied to this Automation.",
    executionAgent: "Execution Agent",
    pipelineHelp: "Azure DevOps Pipeline Agent",
    chooseAgent: "Choose an online Pipeline Agent",
    runPlatform: "Run platform",
    device: "Device",
    choosePlatform: "Choose a supported platform",
    chooseDevice: "Choose an available device",
    runAutomation: "Run automation",
    simulated: "Completed · Simulated",
    noEvidence: "No provider, pipeline, browser, or device was contacted.",
    runHistory: "Automation run history",
    noRuns: "No simulated runs yet",
    triggeredFrom: "Triggered from",
    newHeading: "Create automation",
    title: "Automation title",
    goal: "Describe what to automate",
    automationType: "Automation type",
    chooseType: "Choose Web or Mobile",
    noTestPlan: "No Test Plan",
    generate: "Generate BDD",
    createBlank: "Create blank automation",
    required: "Add a title or goal and choose Web or Mobile.",
    ambiguous: "This could be Web and Mobile. Choose a type to continue.",
    openTestPlan: "Open Test Plan",
    unlinkPlan: "Unlink Test Plan",
    linkPlan: "Link Test Plan",
    implementation: "Implementation mapping",
    localOnly: "Prototype state · stored in this browser",
  },
  zh: {
    heading: "低代码自动化",
    description: "用业务可读的 BDD 设计流程，并把每个步骤绑定到可执行动作。",
    count: "个自动化",
    newAutomation: "新建自动化",
    search: "搜索自动化",
    name: "名称",
    type: "类型",
    testPlan: "测试计划",
    scenarios: "场景",
    lastRun: "最近运行",
    never: "从未运行",
    open: "打开",
    back: "返回自动化列表",
    draft: "草稿",
    ready: "就绪",
    linked: "已关联",
    unlinked: "未关联",
    automationActions: "自动化动作",
    editActions: "编辑自动化动作",
    doneEditing: "完成动作编辑",
    addAction: "添加动作",
    action: "动作",
    target: "定位器或目标",
    value: "输入值",
    mappedFrom: "映射自",
    noActions: "还没有实现动作",
    addStep: "添加 BDD 步骤",
    save: "保存草稿",
    saved: "已保存到本地",
    generatedScript: "生成的脚本",
    run: "运行",
    aiAgent: "AI 智能体",
    agentIntro: "与平台智能体讨论当前自动化；应用前不会覆盖 BDD。",
    messageAgent: "向自动化 AI 智能体发送消息",
    propose: "提出修改建议",
    proposal: "建议修改：新增缺少健康告知时的校验场景。",
    apply: "应用建议",
    reject: "拒绝",
    applied: "建议已应用到当前自动化。",
    executionAgent: "执行 Agent",
    pipelineHelp: "Azure DevOps Pipeline Agent",
    chooseAgent: "请选择在线的 Pipeline Agent",
    runPlatform: "运行平台",
    device: "设备",
    choosePlatform: "请选择支持的平台",
    chooseDevice: "请选择可用设备",
    runAutomation: "执行自动化",
    simulated: "已完成 · 模拟运行",
    noEvidence: "未连接真实 Provider、Pipeline、浏览器或设备。",
    runHistory: "自动化运行历史",
    noRuns: "还没有模拟运行",
    triggeredFrom: "发起位置",
    newHeading: "创建自动化",
    title: "自动化标题",
    goal: "描述你想自动化的内容",
    automationType: "自动化类型",
    chooseType: "选择 Web 或 Mobile",
    noTestPlan: "不关联测试计划",
    generate: "生成 BDD",
    createBlank: "创建空白自动化",
    required: "请填写标题或目标，并选择 Web 或 Mobile。",
    ambiguous: "目标可能同时包含 Web 和 Mobile，请先明确选择类型。",
    openTestPlan: "打开测试计划",
    unlinkPlan: "解除测试计划关联",
    linkPlan: "关联测试计划",
    implementation: "实现映射",
    localOnly: "原型状态 · 保存在当前浏览器",
  },
} as const;

const ACTION_TYPES: readonly ImplementationActionType[] = [
  "Navigate",
  "Click",
  "Send keys",
  "Select",
  "Wait",
  "Assert",
];

function formatRunTime(timestamp: string, locale: Locale): string {
  return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

function actionCode(action: ImplementationAction): string {
  const target = JSON.stringify(action.target);
  const value = JSON.stringify(action.value);
  if (action.action === "Navigate") return `await page.goto(${target});`;
  if (action.action === "Click")
    return `await page.locator(${target}).click();`;
  if (action.action === "Send keys") {
    return `await page.locator(${target}).fill(${value});`;
  }
  if (action.action === "Select") {
    return `await page.locator(${target}).selectOption(${value});`;
  }
  if (action.action === "Assert") {
    return `await expect(page.locator(${target})).toContainText(${value});`;
  }
  return `await page.locator(${target}).waitFor();`;
}

function RunHistory({
  automation,
  runs,
  locale,
  label,
}: {
  automation: Automation;
  runs: readonly AutomationRun[];
  locale: Locale;
  label: string;
}) {
  const text = TEXT[locale];
  return (
    <section className="tap-run-history" aria-label={label}>
      <div className="tap-panel-heading">
        <h3>{label}</h3>
        <span>{runs.length}</span>
      </div>
      {runs.length === 0 ? (
        <p className="tap-empty-note">{text.noRuns}</p>
      ) : (
        <ol>
          {runs.map((run, runIndex) => (
            <li key={run.id}>
              <details open={runIndex === 0}>
                <summary>
                  <span>
                    <strong>{run.id}</strong>
                    <small>{formatRunTime(run.startedAt, locale)}</small>
                  </span>
                  <span className="tap-run-status">{text.simulated}</span>
                </summary>
                <div className="tap-run-detail">
                  <p>
                    {run.target.label} · {text.triggeredFrom}{" "}
                    {run.triggeredFrom}
                  </p>
                  {run.scenarioResults.map((scenarioResult) => {
                    const scenario = automation.feature.scenarios.find(
                      ({ id }) => id === scenarioResult.bddScenarioId,
                    );
                    if (scenario === undefined) return null;
                    return (
                      <div
                        className="tap-run-scenario"
                        key={scenarioResult.bddScenarioId}
                      >
                        <strong>{scenario.title}</strong>
                        {scenarioResult.steps.map((stepResult) => {
                          const step = scenario.steps.find(
                            ({ id }) => id === stepResult.bddStepId,
                          );
                          if (step === undefined) return null;
                          return (
                            <div
                              className="tap-run-step"
                              key={stepResult.bddStepId}
                            >
                              <span>
                                {step.keyword} {step.text}
                              </span>
                              <small>
                                {step.actions
                                  .map(({ action }) => action)
                                  .join(" → ") || text.noActions}
                              </small>
                            </div>
                          );
                        })}
                      </div>
                    );
                  })}
                  <p className="tap-simulation-note">{text.noEvidence}</p>
                </div>
              </details>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function AutomationRunPanel({
  automation,
  runs,
  locale,
  triggeredFrom,
  historyLabel,
  onRun,
}: {
  automation: Automation;
  runs: readonly AutomationRun[];
  locale: Locale;
  triggeredFrom: AutomationRun["triggeredFrom"];
  historyLabel?: string;
  onRun: (
    automation: Automation,
    target: ExecutionTarget,
    triggeredFrom: AutomationRun["triggeredFrom"],
  ) => void;
}) {
  const text = TEXT[locale];
  const [agentId, setAgentId] = useState("");
  const [platform, setPlatform] = useState<MobilePlatform | "">("");
  const [deviceId, setDeviceId] = useState("");
  const availableDevices = mobileDevices.filter(
    (device) => device.platform === platform,
  );
  const selectedAgent = executionAgents.find(({ id }) => id === agentId);
  const selectedDevice = mobileDevices.find(({ id }) => id === deviceId);
  const canRun =
    automation.type === "web"
      ? selectedAgent?.status === "online"
      : platform !== "" &&
        automation.supportedPlatforms.includes(platform) &&
        selectedDevice?.status === "available" &&
        selectedDevice.platform === platform;

  const run = () => {
    if (!canRun) return;
    const target: ExecutionTarget =
      automation.type === "web"
        ? {
            kind: "web",
            executionAgentId: selectedAgent!.id,
            label: selectedAgent!.name,
          }
        : {
            kind: "mobile",
            platform: platform as MobilePlatform,
            deviceId: selectedDevice!.id,
            label: selectedDevice!.name,
          };
    onRun(automation, target, triggeredFrom);
  };

  return (
    <div className="tap-run-panel">
      <div className="tap-run-target">
        {automation.type === "web" ? (
          <label>
            <span>{text.executionAgent}</span>
            <select
              aria-label={text.executionAgent}
              name="execution-agent"
              value={agentId}
              onChange={(event) => setAgentId(event.target.value)}
            >
              <option value="">{text.chooseAgent}</option>
              {executionAgents.map((agent) => (
                <option
                  key={agent.id}
                  value={agent.id}
                  disabled={agent.status !== "online"}
                >
                  {agent.name} · {agent.status}
                </option>
              ))}
            </select>
            <small>{text.pipelineHelp}</small>
          </label>
        ) : (
          <>
            <label>
              <span>{text.runPlatform}</span>
              <select
                aria-label={text.runPlatform}
                name="run-platform"
                value={platform}
                onChange={(event) => {
                  setPlatform(event.target.value as MobilePlatform | "");
                  setDeviceId("");
                }}
              >
                <option value="">{text.choosePlatform}</option>
                {automation.supportedPlatforms.map((item) => (
                  <option key={item} value={item}>
                    {item === "ios" ? "iOS" : "Android"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>{text.device}</span>
              <select
                aria-label={text.device}
                name="run-device"
                value={deviceId}
                onChange={(event) => setDeviceId(event.target.value)}
              >
                <option value="">{text.chooseDevice}</option>
                {availableDevices.map((device) => (
                  <option
                    key={device.id}
                    value={device.id}
                    disabled={device.status !== "available"}
                  >
                    {device.name} · {device.status}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}
        <Button type="primary" disabled={!canRun} onClick={run}>
          {text.runAutomation}
        </Button>
        <p className="tap-simulation-note">Simulated · No execution evidence</p>
      </div>
      <RunHistory
        automation={automation}
        runs={runs}
        locale={locale}
        label={historyLabel ?? text.runHistory}
      />
    </div>
  );
}

function BddBuilder({
  automation,
  locale,
  onUpdate,
}: {
  automation: Automation;
  locale: Locale;
  onUpdate: (automation: Automation) => void;
}) {
  const text = TEXT[locale];
  const [scenarioId, setScenarioId] = useState(
    automation.feature.scenarios[0]?.id ?? "",
  );
  const [editingStepId, setEditingStepId] = useState<string | null>(null);
  const scenario =
    automation.feature.scenarios.find(({ id }) => id === scenarioId) ??
    automation.feature.scenarios[0];

  const replaceScenario = (nextScenario: NonNullable<typeof scenario>) => {
    onUpdate({
      ...automation,
      revision: automation.revision + 1,
      updatedAt: new Date().toISOString(),
      feature: {
        ...automation.feature,
        scenarios: automation.feature.scenarios.map((item) =>
          item.id === nextScenario.id ? nextScenario : item,
        ),
      },
    });
  };

  const updateAction = (
    stepId: string,
    actionId: string,
    patch: Partial<ImplementationAction>,
  ) => {
    if (scenario === undefined) return;
    replaceScenario({
      ...scenario,
      steps: scenario.steps.map((step) =>
        step.id === stepId
          ? {
              ...step,
              actions: step.actions.map((action) =>
                action.id === actionId ? { ...action, ...patch } : action,
              ),
            }
          : step,
      ),
    });
  };

  const addAction = (stepId: string) => {
    if (scenario === undefined) return;
    replaceScenario({
      ...scenario,
      steps: scenario.steps.map((step) =>
        step.id === stepId
          ? {
              ...step,
              actions: [
                ...step.actions,
                {
                  id: `${step.id}-ACT-${String(step.actions.length + 1).padStart(2, "0")}`,
                  bddStepId: step.id,
                  action: "Click",
                  target: "",
                  value: "",
                },
              ],
            }
          : step,
      ),
    });
  };

  const addBddStep = (keyword: BddKeyword = "And") => {
    if (scenario === undefined) return;
    const id = `${automation.id}-ST-M-${automation.revision + 1}-${scenario.steps.length + 1}`;
    replaceScenario({
      ...scenario,
      steps: [
        ...scenario.steps,
        {
          id,
          keyword,
          text: "Describe the expected behavior",
          sourceTestPlanStepId: null,
          actions: [],
        },
      ],
    });
  };

  if (scenario === undefined) {
    return <p className="tap-empty-note">No BDD scenarios yet.</p>;
  }

  return (
    <div className="tap-bdd-workbench">
      <aside className="tap-scenario-navigation" aria-label="Scenarios">
        <span>{text.scenarios}</span>
        {automation.feature.scenarios.map((item, index) => (
          <button
            key={item.id}
            type="button"
            aria-current={item.id === scenario.id ? "page" : undefined}
            onClick={() => setScenarioId(item.id)}
          >
            <small>{String(index + 1).padStart(2, "0")}</small>
            <span>{item.title}</span>
          </button>
        ))}
      </aside>

      <section className="tap-bdd-canvas" aria-label="BDD Builder">
        <div className="tap-bdd-heading">
          <div>
            <span>Scenario</span>
            <h2>{scenario.title}</h2>
          </div>
          <div className="tap-bdd-heading-actions">
            <span className="tap-trace-badge">
              {scenario.sourceTestPlanScenarioId ?? text.unlinked}
            </span>
            <Button
              aria-label={text.addStep}
              icon={<PlusOutlined />}
              onClick={() => addBddStep()}
            >
              {text.addStep}
            </Button>
          </div>
        </div>
        <ol className="tap-bdd-step-list">
          {scenario.steps.map((step, stepIndex) => (
            <li key={step.id}>
              <article
                className="tap-bdd-step"
                aria-label={`BDD step ${stepIndex + 1}`}
              >
                <div className="tap-bdd-step-copy">
                  <label>
                    <span className="tap-bdd-keyword">{step.keyword}</span>
                    <Input
                      aria-label={`BDD step text ${stepIndex + 1}`}
                      name={`bdd-step-${step.id}`}
                      value={step.text}
                      onChange={(event) =>
                        replaceScenario({
                          ...scenario,
                          steps: scenario.steps.map((item) =>
                            item.id === step.id
                              ? { ...item, text: event.target.value }
                              : item,
                          ),
                        })
                      }
                    />
                  </label>
                  <small>
                    {text.mappedFrom}{" "}
                    {step.sourceTestPlanStepId ?? text.unlinked}
                  </small>
                </div>

                <div className="tap-action-binding">
                  <div className="tap-action-binding-heading">
                    <div>
                      <strong>{text.automationActions}</strong>
                      <span>{text.implementation}</span>
                    </div>
                    <Button
                      type="text"
                      onClick={() =>
                        setEditingStepId((current) =>
                          current === step.id ? null : step.id,
                        )
                      }
                    >
                      {editingStepId === step.id
                        ? text.doneEditing
                        : `${text.editActions} ${stepIndex + 1}`}
                    </Button>
                  </div>
                  {step.actions.length === 0 ? (
                    <p>{text.noActions}</p>
                  ) : (
                    <ol className="tap-action-chain">
                      {step.actions.map((action, actionIndex) => (
                        <li key={action.id}>
                          <span className="tap-action-type">
                            {action.action}
                          </span>
                          <span className="tap-action-target">
                            {action.target || "—"}
                          </span>
                          {action.value ? <code>{action.value}</code> : null}
                          {editingStepId === step.id ? (
                            <div className="tap-action-editor">
                              <label>
                                <span>{text.action}</span>
                                <select
                                  aria-label={`${text.action} ${actionIndex + 1} for BDD step ${stepIndex + 1}`}
                                  data-bdd-step-id={step.id}
                                  value={action.action}
                                  onChange={(event) =>
                                    updateAction(step.id, action.id, {
                                      action: event.target
                                        .value as ImplementationActionType,
                                    })
                                  }
                                >
                                  {ACTION_TYPES.map((actionType) => (
                                    <option key={actionType}>
                                      {actionType}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              <label>
                                <span>{text.target}</span>
                                <Input
                                  aria-label={`${text.target} ${actionIndex + 1} for BDD step ${stepIndex + 1}`}
                                  value={action.target}
                                  onChange={(event) =>
                                    updateAction(step.id, action.id, {
                                      target: event.target.value,
                                    })
                                  }
                                />
                              </label>
                              <label>
                                <span>{text.value}</span>
                                <Input
                                  aria-label={`${text.value} ${actionIndex + 1} for BDD step ${stepIndex + 1}`}
                                  value={action.value}
                                  onChange={(event) =>
                                    updateAction(step.id, action.id, {
                                      value: event.target.value,
                                    })
                                  }
                                />
                              </label>
                            </div>
                          ) : null}
                        </li>
                      ))}
                    </ol>
                  )}
                  {editingStepId === step.id ? (
                    <Button
                      aria-label={`${text.addAction} for BDD step ${stepIndex + 1}`}
                      icon={<PlusOutlined />}
                      onClick={() => addAction(step.id)}
                    >
                      {text.addAction}
                    </Button>
                  ) : null}
                </div>
              </article>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function AutomationDetail({
  state,
  automation,
  locale,
  onBack,
  onUpdate,
  onLink,
  onOpenTestPlan,
  onRun,
}: {
  state: ArtifactState;
  automation: Automation;
  locale: Locale;
  onBack: () => void;
  onUpdate: (automation: Automation) => void;
  onLink: (automationId: string, testPlanId: string | null) => void;
  onOpenTestPlan: (testPlanId: string) => void;
  onRun: (
    automation: Automation,
    target: ExecutionTarget,
    triggeredFrom: AutomationRun["triggeredFrom"],
  ) => void;
}) {
  const text = TEXT[locale];
  const [panel, setPanel] = useState<"run" | "agent">("run");
  const [saved, setSaved] = useState(false);
  const [agentMessage, setAgentMessage] = useState("");
  const [proposal, setProposal] = useState<"none" | "pending" | "applied">(
    "none",
  );
  const [planChoice, setPlanChoice] = useState("");
  const linkedPlan = state.testPlans.find(
    ({ id }) => id === automation.testPlanId,
  );
  const availablePlans = selectAvailableTestPlans(state, automation.id);
  const runs = selectAutomationRuns(state, automation.id);
  const code = useMemo(
    () =>
      automation.feature.scenarios
        .flatMap((scenario) => scenario.steps)
        .flatMap((step) => step.actions)
        .map((action) => `  ${actionCode(action)}`)
        .join("\n"),
    [automation.feature.scenarios],
  );

  const applyProposal = () => {
    const scenarioOrdinal = automation.feature.scenarios.length + 1;
    const scenarioId = `${automation.id}-SC-${String(scenarioOrdinal).padStart(2, "0")}`;
    const firstStepId = `${automation.id}-ST-AI-01`;
    onUpdate({
      ...automation,
      revision: automation.revision + 1,
      updatedAt: new Date().toISOString(),
      feature: {
        ...automation.feature,
        scenarios: [
          ...automation.feature.scenarios,
          {
            id: scenarioId,
            title: "Missing health disclosure validation",
            sourceTestPlanScenarioId: null,
            steps: [
              {
                id: firstStepId,
                keyword: "Then",
                text: "the missing health disclosures are shown",
                sourceTestPlanStepId: null,
                actions: [
                  {
                    id: `${firstStepId}-ACT-01`,
                    bddStepId: firstStepId,
                    action: "Assert",
                    target: "[data-testid='health-disclosure-error']",
                    value: "Required",
                  },
                ],
              },
            ],
          },
        ],
      },
    });
    setProposal("applied");
  };

  return (
    <section
      className="tap-module tap-automation-detail"
      aria-labelledby="automation-detail-heading"
    >
      <header className="tap-automation-detail-header">
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>
          {text.back}
        </Button>
        <div className="tap-automation-title-row">
          <div>
            <span className="tap-asset-id">{automation.id}</span>
            <h1 id="automation-detail-heading">{automation.title}</h1>
            <p>
              {automation.type === "web" ? "Web" : "Mobile"} ·{" "}
              {automation.status === "ready" ? text.ready : text.draft} ·{" "}
              {text.localOnly}
            </p>
          </div>
          <div className="tap-heading-actions">
            {saved ? (
              <span className="tap-saved-state" role="status">
                {text.saved}
              </span>
            ) : null}
            <Button onClick={() => setSaved(true)}>{text.save}</Button>
          </div>
        </div>
        <div className="tap-asset-relation">
          <LinkOutlined aria-hidden="true" />
          {linkedPlan === undefined ? (
            <>
              <select
                aria-label={text.testPlan}
                value={planChoice}
                onChange={(event) => setPlanChoice(event.target.value)}
              >
                <option value="">{text.noTestPlan}</option>
                {availablePlans.map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.id} · {plan.title}
                  </option>
                ))}
              </select>
              <Button
                disabled={planChoice === ""}
                onClick={() => onLink(automation.id, planChoice)}
              >
                {text.linkPlan}
              </Button>
            </>
          ) : (
            <>
              <span>
                {text.linked} · {linkedPlan.id} · {linkedPlan.title}
              </span>
              <Button onClick={() => onOpenTestPlan(linkedPlan.id)}>
                {text.openTestPlan} {linkedPlan.id}
              </Button>
              <Button type="text" onClick={() => onLink(automation.id, null)}>
                {text.unlinkPlan}
              </Button>
            </>
          )}
        </div>
      </header>

      <div className="tap-automation-workspace-grid">
        <BddBuilder
          automation={automation}
          locale={locale}
          onUpdate={onUpdate}
        />
        <aside className="tap-automation-side-panel">
          <div
            className="tap-panel-tabs"
            role="tablist"
            aria-label="Automation tools"
          >
            <button
              type="button"
              role="tab"
              aria-selected={panel === "run"}
              onClick={() => setPanel("run")}
            >
              {text.run}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={panel === "agent"}
              onClick={() => setPanel("agent")}
            >
              {text.aiAgent}
            </button>
          </div>
          {panel === "run" ? (
            <AutomationRunPanel
              automation={automation}
              runs={runs}
              locale={locale}
              triggeredFrom="automation"
              onRun={onRun}
            />
          ) : (
            <section
              className="tap-agent-panel"
              aria-label="Automation AI Agent"
            >
              <RobotOutlined aria-hidden="true" />
              <p>{text.agentIntro}</p>
              <label>
                <span>{text.messageAgent}</span>
                <Input.TextArea
                  value={agentMessage}
                  onChange={(event) => setAgentMessage(event.target.value)}
                  rows={4}
                />
              </label>
              <Button
                type="primary"
                disabled={agentMessage.trim() === ""}
                onClick={() => setProposal("pending")}
              >
                {text.propose}
              </Button>
              {proposal === "pending" ? (
                <div className="tap-agent-proposal">
                  <p>{text.proposal}</p>
                  <div>
                    <Button onClick={() => setProposal("none")}>
                      {text.reject}
                    </Button>
                    <Button type="primary" onClick={applyProposal}>
                      {text.apply}
                    </Button>
                  </div>
                </div>
              ) : null}
              {proposal === "applied" ? (
                <p role="status">{text.applied}</p>
              ) : null}
            </section>
          )}
        </aside>
      </div>

      <details className="tap-script-disclosure">
        <summary>{text.generatedScript}</summary>
        <pre>
          <code>{`test("${automation.goal}", async ({ page }) => {\n${code}\n});`}</code>
        </pre>
      </details>
    </section>
  );
}

function AutomationLibrary({
  state,
  locale,
  onNew,
  onOpen,
}: {
  state: ArtifactState;
  locale: Locale;
  onNew: () => void;
  onOpen: (automationId: string) => void;
}) {
  const text = TEXT[locale];
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const automations = state.automations.filter((automation) =>
    automation.title.toLowerCase().includes(normalized),
  );
  return (
    <section
      className="tap-module tap-automation-library"
      aria-labelledby="low-code-heading"
    >
      <header className="tap-module-heading">
        <div>
          <h1 id="low-code-heading">{text.heading}</h1>
          <p>{text.description}</p>
        </div>
        <Button
          aria-label={text.newAutomation}
          type="primary"
          icon={<PlusOutlined />}
          onClick={onNew}
        >
          {text.newAutomation}
        </Button>
      </header>
      <div className="tap-library-summary">
        <strong>{state.automations.length}</strong>
        <span>{text.count}</span>
        <Input.Search
          aria-label={text.search}
          placeholder={text.search}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div
        className="tap-automation-table"
        role="table"
        aria-label={text.heading}
      >
        <div
          className="tap-automation-row tap-automation-row--header"
          role="row"
        >
          <span role="columnheader">{text.name}</span>
          <span role="columnheader">{text.type}</span>
          <span role="columnheader">{text.testPlan}</span>
          <span role="columnheader">{text.scenarios}</span>
          <span role="columnheader">{text.lastRun}</span>
          <span role="columnheader" aria-label={text.open} />
        </div>
        {automations.map((automation) => {
          const runs = selectAutomationRuns(state, automation.id);
          return (
            <div className="tap-automation-row" role="row" key={automation.id}>
              <span role="cell">
                <CodeOutlined aria-hidden="true" />
                <span>
                  <strong>{automation.title}</strong>
                  <small>{automation.id}</small>
                </span>
              </span>
              <span role="cell">
                <span className="tap-type-badge">
                  {automation.type === "web" ? "Web" : "Mobile"}
                </span>
              </span>
              <span role="cell">{automation.testPlanId ?? text.unlinked}</span>
              <span role="cell">{automation.feature.scenarios.length}</span>
              <span role="cell">
                {runs[0] === undefined
                  ? text.never
                  : formatRunTime(runs[0].startedAt, locale)}
              </span>
              <span role="cell">
                <Button onClick={() => onOpen(automation.id)}>
                  {text.open} {automation.id}
                </Button>
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function NewAutomation({
  state,
  locale,
  onBack,
  onCreate,
}: {
  state: ArtifactState;
  locale: Locale;
  onBack: () => void;
  onCreate: (draft: {
    title: string;
    goal: string;
    type: AutomationType;
    testPlanId: string | null;
    mode: "blank" | "generated";
  }) => void;
}) {
  const text = TEXT[locale];
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [type, setType] = useState<AutomationType | "">("");
  const [testPlanId, setTestPlanId] = useState("");
  const [error, setError] = useState<"missing" | "ambiguous" | null>(null);
  const availablePlans = state.testPlans.filter(
    ({ automationId }) => automationId === null,
  );
  const create = (mode: "blank" | "generated") => {
    const normalizedTitle = title.trim();
    const normalizedGoal = goal.trim();
    const intent = `${normalizedTitle} ${normalizedGoal}`.trim();
    let inferredType = type;
    const indicatesWeb = /\b(browser|web|playwright)\b/i.test(intent);
    const indicatesMobile = /\b(mobile|ios|android|device)\b/i.test(intent);
    if (mode === "generated" && inferredType === "") {
      if (indicatesWeb && indicatesMobile) {
        setError("ambiguous");
        return;
      }
      if (indicatesWeb && !indicatesMobile) inferredType = "web";
      if (indicatesMobile && !indicatesWeb) inferredType = "mobile";
    }
    if (intent === "" || inferredType === "") {
      setError("missing");
      return;
    }
    setError(null);
    onCreate({
      title: normalizedTitle || normalizedGoal,
      goal: normalizedGoal || normalizedTitle,
      type: inferredType,
      testPlanId: testPlanId || null,
      mode,
    });
  };
  return (
    <section
      className="tap-module tap-new-automation"
      aria-labelledby="new-automation-heading"
    >
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>
        {text.back}
      </Button>
      <header>
        <h1 id="new-automation-heading">{text.newHeading}</h1>
        <p>{text.description}</p>
      </header>
      <div className="tap-new-automation-form">
        <label>
          <span>{text.title}</span>
          <Input
            aria-label={text.title}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          <span>{text.goal}</span>
          <Input.TextArea
            aria-label={text.goal}
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            rows={4}
          />
        </label>
        <label>
          <span>{text.automationType}</span>
          <select
            aria-label={text.automationType}
            value={type}
            onChange={(event) =>
              setType(event.target.value as AutomationType | "")
            }
          >
            <option value="">{text.chooseType}</option>
            <option value="web">Web</option>
            <option value="mobile">Mobile</option>
          </select>
        </label>
        <label>
          <span>{text.testPlan}</span>
          <select
            aria-label={text.testPlan}
            value={testPlanId}
            onChange={(event) => setTestPlanId(event.target.value)}
          >
            <option value="">{text.noTestPlan}</option>
            {availablePlans.map((plan) => (
              <option key={plan.id} value={plan.id}>
                {plan.id} · {plan.title}
              </option>
            ))}
          </select>
        </label>
        {error ? (
          <p role="alert">
            {error === "ambiguous" ? text.ambiguous : text.required}
          </p>
        ) : null}
        <div>
          <Button onClick={() => create("blank")}>{text.createBlank}</Button>
          <Button
            aria-label={text.generate}
            type="primary"
            icon={<ThunderboltOutlined />}
            onClick={() => create("generated")}
          >
            {text.generate}
          </Button>
        </div>
      </div>
    </section>
  );
}

export function AutomationWorkspace({
  state,
  view,
  locale,
  onViewChange,
  onUpdate,
  onCreate,
  onLink,
  onOpenTestPlan,
  onRun,
}: {
  state: ArtifactState;
  view: AutomationWorkspaceView;
  locale: Locale;
  onViewChange: (view: AutomationWorkspaceView) => void;
  onUpdate: (automation: Automation) => void;
  onCreate: (draft: {
    title: string;
    goal: string;
    type: AutomationType;
    testPlanId: string | null;
    mode: "blank" | "generated";
  }) => void;
  onLink: (automationId: string, testPlanId: string | null) => void;
  onOpenTestPlan: (testPlanId: string) => void;
  onRun: (
    automation: Automation,
    target: ExecutionTarget,
    triggeredFrom: AutomationRun["triggeredFrom"],
  ) => void;
}) {
  if (view.kind === "library") {
    return (
      <AutomationLibrary
        state={state}
        locale={locale}
        onNew={() => onViewChange({ kind: "new" })}
        onOpen={(automationId) =>
          onViewChange({ kind: "detail", automationId })
        }
      />
    );
  }
  if (view.kind === "new") {
    return (
      <NewAutomation
        state={state}
        locale={locale}
        onBack={() => onViewChange({ kind: "library" })}
        onCreate={onCreate}
      />
    );
  }
  const automation = state.automations.find(
    ({ id }) => id === view.automationId,
  );
  if (automation === undefined) {
    return (
      <section className="tap-module tap-not-found">
        <h1>Automation not found</h1>
        <Button onClick={() => onViewChange({ kind: "library" })}>
          {TEXT[locale].back}
        </Button>
      </section>
    );
  }
  return (
    <AutomationDetail
      state={state}
      automation={automation}
      locale={locale}
      onBack={() => onViewChange({ kind: "library" })}
      onUpdate={onUpdate}
      onLink={onLink}
      onOpenTestPlan={onOpenTestPlan}
      onRun={onRun}
    />
  );
}
