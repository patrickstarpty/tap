import type {
  ArtifactState,
  Automation,
  AutomationRun,
} from "./artifacts/model";
import type { AutomationWorkspaceView } from "./automation/AutomationWorkspace";
import type { Locale, ProductModule } from "./model";

export interface FloatingAssistantContext {
  key: string;
  label: string;
  summary: string;
  facts: readonly string[];
  prompts: readonly string[];
}

export function getFloatingAssistantContext({
  activeModule,
  selectedPlanId,
  automationView,
  artifacts,
  locale,
}: {
  activeModule: ProductModule;
  selectedPlanId: string | null;
  automationView: AutomationWorkspaceView;
  artifacts: ArtifactState;
  locale: Locale;
}): FloatingAssistantContext {
  const t = (en: string, zh: string) => (locale === "zh" ? zh : en);
  const count = (value: number, en: string, zh: string) =>
    t(`${value} ${en}${value === 1 ? "" : "s"}`, `${value} ${zh}`);
  const name = (asset: { title: string; id: string }) =>
    `${asset.title} (${asset.id})`;
  const explainPrompt = t("Explain this page", "解释当前页面");
  const coveragePrompt = t("Review coverage gaps", "梳理覆盖范围与缺口");
  const explorePrompt = t("Suggest exploratory checks", "建议探索性测试方向");
  const runPrompt = t("Prepare a run checklist", "整理运行前检查项");
  const planPrompts = [explainPrompt, coveragePrompt, explorePrompt];
  const automationPrompts = [explainPrompt, coveragePrompt, runPrompt];
  const runFact = (runs: readonly AutomationRun[]) => {
    const latest = [...runs].sort((a, b) =>
      b.startedAt.localeCompare(a.startedAt),
    )[0];
    if (!latest)
      return t(
        "No simulated runs recorded for this context.",
        "当前上下文尚无模拟运行记录。",
      );
    const status =
      latest.status === "completed"
        ? t("completed", "已完成")
        : t("error", "错误");
    return t(
      `Simulated runs: ${runs.length}; latest ${latest.id} · ${status} · ${latest.target.label} · ${latest.startedAt}; no real execution evidence.`,
      `模拟运行：${runs.length} 次；最近一次 ${latest.id} · ${status} · ${latest.target.label} · ${latest.startedAt}；不含真实执行证据。`,
    );
  };
  const implementationFacts = (automation: Automation) => {
    const steps = automation.feature.scenarios.flatMap(
      (scenario) => scenario.steps,
    );
    const gaps = steps.filter((step) => step.actions.length === 0);
    const actions = steps.flatMap((step) => step.actions);
    const emptyTargets = actions.filter((action) => !action.target.trim());
    return [
      t(
        `${count(actions.length, "implementation action", "个实现动作")}; ${count(gaps.length, "step", "个步骤")} without implementation actions; ${count(emptyTargets.length, "action", "个动作")} without a target.`,
        `${actions.length} 个实现动作；${gaps.length} 个步骤未配置实现动作；${emptyTargets.length} 个动作缺少目标。`,
      ),
      ...gaps.map((step) =>
        t(
          `Unmapped step: ${step.text} (${step.id})`,
          `未实现步骤：${step.text} (${step.id})`,
        ),
      ),
    ];
  };
  const missing = (
    key: string,
    id: string,
    prompts: readonly string[],
  ): FloatingAssistantContext => ({
    key,
    label: id,
    summary: t(
      "The selected item is no longer available in the loaded page data.",
      "所选条目已不在当前加载的页面数据中。",
    ),
    facts: [],
    prompts,
  });

  if (activeModule === "test-management") {
    if (selectedPlanId) {
      const key = `test-plan:${selectedPlanId}`;
      const plan = artifacts.testPlans.find(
        (item) => item.id === selectedPlanId,
      );
      if (!plan) return missing(key, selectedPlanId, planPrompts);
      const automation = artifacts.automations.find(
        (item) => item.id === plan.automationId,
      );
      return {
        key,
        label: `${plan.id} · ${plan.title}`,
        summary: `${name(plan)} · ${count(plan.scenarios.length, "scenario", "个场景")} · ${count(
          plan.scenarios.reduce(
            (total, scenario) => total + scenario.steps.length,
            0,
          ),
          "step",
          "个步骤",
        )}`,
        facts: [
          automation
            ? t(
                `Linked automation: ${name(automation)}`,
                `关联自动化：${name(automation)}`,
              )
            : plan.automationId
              ? t(
                  `Linked automation ${plan.automationId} is unavailable.`,
                  `关联自动化 ${plan.automationId} 不可用。`,
                )
              : t("No linked automation.", "未关联自动化。"),
          ...plan.scenarios.map((scenario) =>
            t(`Scenario: ${name(scenario)}`, `场景：${name(scenario)}`),
          ),
          ...(automation ? implementationFacts(automation) : []),
          runFact(
            artifacts.runs.filter((run) => run.testPlanIdAtRun === plan.id),
          ),
        ],
        prompts: planPrompts,
      };
    }
    const unlinked = artifacts.testPlans.filter(
      (plan) => !plan.automationId,
    ).length;
    return {
      key: "test-management:library",
      label: t("Test Management", "测试管理"),
      summary: t(
        `${count(artifacts.testPlans.length, "Test Plan", "个测试计划")} in the loaded library.`,
        `当前已加载 ${artifacts.testPlans.length} 个测试计划。`,
      ),
      facts: [
        t(
          `${unlinked} without linked automation.`,
          `${unlinked} 个未关联自动化。`,
        ),
        ...artifacts.testPlans.map(
          (plan) =>
            `${name(plan)} · ${count(plan.scenarios.length, "scenario", "个场景")}`,
        ),
      ],
      prompts: planPrompts,
    };
  }

  if (activeModule === "low-code") {
    if (automationView.kind === "detail") {
      const key = `automation:${automationView.automationId}`;
      const automation = artifacts.automations.find(
        (item) => item.id === automationView.automationId,
      );
      if (!automation)
        return missing(key, automationView.automationId, automationPrompts);
      const plan = artifacts.testPlans.find(
        (item) => item.id === automation.testPlanId,
      );
      const mapped =
        plan?.scenarios.filter((scenario) =>
          automation.feature.scenarios.some(
            (item) => item.sourceTestPlanScenarioId === scenario.id,
          ),
        ).length ?? 0;
      return {
        key,
        label: `${automation.id} · ${automation.title}`,
        summary: `${name(automation)} · ${automation.type === "web" ? t("Web", "网页") : t("Mobile", "移动端")} · ${count(automation.feature.scenarios.length, "scenario", "个场景")}`,
        facts: [
          t(`Goal: ${automation.goal}`, `目标：${automation.goal}`),
          plan
            ? t(
                `Linked Test Plan: ${name(plan)}; ${mapped} of ${plan.scenarios.length} Test Plan scenarios mapped.`,
                `关联测试计划：${name(plan)}；${plan.scenarios.length} 个测试计划场景中已映射 ${mapped} 个。`,
              )
            : automation.testPlanId
              ? t(
                  `Linked Test Plan ${automation.testPlanId} is unavailable.`,
                  `关联测试计划 ${automation.testPlanId} 不可用。`,
                )
              : t("No linked Test Plan.", "未关联测试计划。"),
          ...automation.feature.scenarios.map((scenario) =>
            t(`Scenario: ${name(scenario)}`, `场景：${name(scenario)}`),
          ),
          ...implementationFacts(automation),
          runFact(
            artifacts.runs.filter((run) => run.automationId === automation.id),
          ),
        ],
        prompts: automationPrompts,
      };
    }
    if (automationView.kind === "new") {
      const available = artifacts.testPlans.filter(
        (plan) => !plan.automationId,
      );
      return {
        key: "automation:new",
        label: t("New automation", "新建自动化"),
        summary: t(
          "Define an automation goal, choose Web or Mobile, and optionally link a Test Plan.",
          "定义自动化目标，选择网页或移动端，并按需关联测试计划。",
        ),
        facts: [
          t(
            "The creation form has not saved a new automation yet; its unsaved input is not part of this context.",
            "新建表单尚未保存新自动化；此上下文不包含表单中未保存的输入。",
          ),
          t(
            `${available.length} unlinked Test Plans available.`,
            `${available.length} 个未关联的测试计划可选。`,
          ),
          ...available.map(name),
        ],
        prompts: [
          t("What should I define first?", "首先需要定义哪些内容？"),
          t("How should I link a Test Plan?", "如何关联测试计划？"),
          explorePrompt,
        ],
      };
    }
    return {
      key: "automation:library",
      label: t("Automation Library", "自动化库"),
      summary: t(
        `${count(artifacts.automations.length, "automation", "个自动化")} in the loaded library.`,
        `当前已加载 ${artifacts.automations.length} 个自动化。`,
      ),
      facts: [
        t(
          `${artifacts.automations.filter((automation) => !automation.testPlanId).length} without a linked Test Plan.`,
          `${artifacts.automations.filter((automation) => !automation.testPlanId).length} 个未关联测试计划。`,
        ),
        ...artifacts.automations.map(
          (automation) =>
            `${name(automation)} · ${automation.type === "web" ? t("Web", "网页") : t("Mobile", "移动端")}`,
        ),
        runFact(artifacts.runs),
      ],
      prompts: automationPrompts,
    };
  }

  return {
    key: `module:${activeModule}`,
    label: "Tapper",
    summary: t(
      "Contextual suggestions are available on Test Management and Automation pages.",
      "上下文建议适用于测试管理与自动化页面。",
    ),
    facts: [],
    prompts: planPrompts,
  };
}

export function createFloatingAssistantReply(
  prompt: string,
  context: FloatingAssistantContext,
  locale: Locale,
): { text: string; suggestions: readonly string[] } {
  const t = (en: string, zh: string) => (locale === "zh" ? zh : en);
  const normalized = prompt.trim().toLowerCase();
  const category = /\b(run|execution|execute|checklist)\b|运行|执行/.test(
    normalized,
  )
    ? "run"
    : /\b(coverage|gap|gaps|mapping|mapped)\b|覆盖|缺口|映射/.test(normalized)
      ? "coverage"
      : /\b(exploratory|explore|exploration|boundary)\b|探索|边界/.test(
            normalized,
          )
        ? "exploration"
        : /\b(explain|summarize|summary|describe|define|link)\b|解释|说明|总结|定义|关联/.test(
              normalized,
            )
          ? "explanation"
          : "unsupported";
  const guidance = {
    coverage: {
      note: t(
        "These page facts show recorded scenarios and mappings; they do not establish complete requirements coverage.",
        "这些页面事实展示已记录场景和映射，不能证明已覆盖全部需求。",
      ),
      suggestions: [
        t(
          "Compare each recorded scenario with an agreed requirement and expected outcome.",
          "将每个已记录场景与已确认需求和预期结果逐一对照。",
        ),
        t(
          "Review missing links or action mappings before adding further scenarios.",
          "补充场景前，检查未关联项和缺少实现动作的步骤。",
        ),
        t(
          "Consider boundary and negative cases as candidates, then confirm which are relevant.",
          "将边界与异常情况作为候选方向，并确认其适用性。",
        ),
      ],
    },
    exploration: {
      note: t(
        "Use the recorded scenarios to choose exploratory checks; these are ideas to validate, not observed defects.",
        "根据已记录场景选择探索方向；以下是待验证的建议，不是已发现的缺陷。",
      ),
      suggestions: [
        t(
          "Choose one recorded scenario and vary a required input or boundary value.",
          "选择一个已记录场景，变更必填输入或边界值进行探索。",
        ),
        t(
          "Explore interruption, retry, and recovery where the workflow supports them.",
          "在流程适用时探索中断、重试与恢复行为。",
        ),
        t(
          "Record the observed outcome and evidence before proposing a regression scenario.",
          "记录观察结果和证据后，再决定是否增加回归场景。",
        ),
      ],
    },
    run: {
      note: t(
        "This prototype only records simulated runs; it does not execute against a browser or device and provides no real execution evidence.",
        "此原型仅记录模拟运行，不会在浏览器或设备上真实执行，也不提供真实执行证据。",
      ),
      suggestions: [
        t(
          "Confirm the intended automation and its optional Test Plan association.",
          "确认目标自动化及其可选的测试计划关联。",
        ),
        t(
          "Review implementation actions, targets, and expected assertions.",
          "检查实现动作、目标和预期断言。",
        ),
        t(
          "Select the appropriate execution agent or mobile device in the run panel, then inspect the simulated result.",
          "在运行面板选择合适的执行代理或移动设备，然后检查模拟结果。",
        ),
      ],
    },
    explanation: {
      note: t(
        "This explanation uses the loaded page data only.",
        "此说明仅基于当前加载的页面数据。",
      ),
      suggestions:
        context.key === "automation:new"
          ? [
              t(
                "Write the business goal and expected outcome.",
                "明确业务目标和预期结果。",
              ),
              t(
                "Choose Web or Mobile to match the intended application.",
                "根据目标应用选择网页或移动端。",
              ),
              t(
                "Choose an available Test Plan if traceability is needed, then review its scenarios.",
                "如需追溯关系，选择一个可用测试计划并核对其场景。",
              ),
            ]
          : [
              t(
                "Review the listed scenarios and asset links.",
                "核对列出的场景及资产关联。",
              ),
              t(
                "Open an item to inspect its steps and implementation mappings.",
                "打开具体条目，查看步骤和实现映射。",
              ),
              t(
                "Confirm requirements before treating the page as evidence of coverage.",
                "将页面作为覆盖依据前，先确认需求范围。",
              ),
            ],
    },
    unsupported: {
      note: t(
        "This prototype cannot answer this request from page data alone. It can summarize the current page and suggest coverage, exploration, or run checks.",
        "此原型无法仅凭页面数据回答该请求。它可以解释当前页面，并提供覆盖检查、探索或运行前检查建议。",
      ),
      suggestions: context.prompts,
    },
  }[category];
  const relevantFact =
    category === "unsupported"
      ? undefined
      : category === "run"
        ? context.facts.find((fact) =>
            /^(Simulated runs|No simulated runs|模拟运行|当前上下文尚无模拟运行)/.test(
              fact,
            ),
          )
        : category === "exploration"
          ? context.facts.find((fact) => /^(Scenario:|场景：)/.test(fact))
          : category === "coverage"
            ? (context.facts.find((fact) =>
                /No linked|without (?:a )?linked|未关联|unavailable|不可用|Unmapped step|未实现步骤/.test(
                  fact,
                ),
              ) ??
              context.facts.find((fact) =>
                /implementation|实现动作|mapped|映射/.test(fact),
              ))
            : context.facts[0];
  return {
    text: [context.summary, relevantFact, guidance.note]
      .filter(Boolean)
      .join("\n\n"),
    suggestions: guidance.suggestions,
  };
}
