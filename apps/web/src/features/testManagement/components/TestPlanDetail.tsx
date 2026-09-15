import type { TestPlanRevision } from "../api/client";

const COPY = {
  en: {
    status: {
      DRAFT: "Draft",
      VALIDATING: "Validating",
      PUBLISHED: "Published",
      SUPERSEDED: "Superseded",
    },
    cases: "Test cases",
    evidence: "Source evidence",
    scope: "Scope",
    prerequisites: "Prerequisites",
    risks: "Risks",
    assumptions: "Assumptions",
    unknowns: "To confirm",
    gaps: "Coverage gaps",
    critical: "Critical",
    expected: "Expected result",
    provenance: "View evidence identifiers",
    sourceOrigin: "Document source",
    graphOrigin: "Graph extracted",
    severity: {
      LOW: "Low",
      MEDIUM: "Medium",
      HIGH: "High",
      CRITICAL: "Critical",
    },
    caseCount: "cases",
    evidenceCount: "source citations",
    gapCount: "coverage gaps",
  },
  zh: {
    status: {
      DRAFT: "草稿",
      VALIDATING: "验证中",
      PUBLISHED: "已发布",
      SUPERSEDED: "已取代",
    },
    cases: "测试用例",
    evidence: "来源依据",
    scope: "测试范围",
    prerequisites: "前置条件",
    risks: "风险",
    assumptions: "假设",
    unknowns: "待确认",
    gaps: "覆盖缺口",
    critical: "关键",
    expected: "预期结果",
    provenance: "查看证据标识",
    sourceOrigin: "文档来源",
    graphOrigin: "图谱提取",
    severity: { LOW: "低", MEDIUM: "中", HIGH: "高", CRITICAL: "关键" },
    caseCount: "个用例",
    evidenceCount: "条来源依据",
    gapCount: "个覆盖缺口",
  },
} as const;

const readable = (value: string) => value.replace(/[-_]+/g, " ");

export function TestPlanDetail({
  plan,
  locale,
}: {
  plan: TestPlanRevision;
  locale: "en" | "zh";
}) {
  const text = COPY[locale];
  return (
    <article className="tap-plan-detail" aria-labelledby="test-plan-title">
      <header className="tap-plan-detail-header">
        <span className="tap-plan-status">{text.status[plan.status]}</span>
        <h1 id="test-plan-title">{readable(plan.title)}</h1>
        <p>{plan.objective}</p>
        <div
          className="tap-plan-detail-stats"
          aria-label={locale === "zh" ? "计划概览" : "Plan overview"}
        >
          <span>
            <strong>{plan.cases.length}</strong> {text.caseCount}
          </span>
          <span>
            <strong>{plan.citations.length}</strong> {text.evidenceCount}
          </span>
          <span>
            <strong>{plan.coverageGaps.length}</strong> {text.gapCount}
          </span>
        </div>
      </header>

      <div className="tap-plan-detail-context">
        {(
          [
            [text.scope, plan.scopeItems],
            [text.prerequisites, plan.prerequisites],
            [text.risks, plan.risks],
          ] as const
        ).map(([heading, values]) =>
          values.length > 0 ? (
            <section key={heading} className="tap-plan-detail-context-card">
              <h2>{heading}</h2>
              <ul>
                {values.map((value) => (
                  <li key={value}>{readable(value)}</li>
                ))}
              </ul>
            </section>
          ) : null,
        )}
      </div>

      <section
        className="tap-plan-detail-section"
        aria-labelledby="test-cases-title"
      >
        <h2 id="test-cases-title">{text.cases}</h2>
        <div className="tap-plan-case-list">
          {plan.cases.map((testCase) => (
            <article className="tap-plan-case" key={testCase.caseId}>
              <div className="tap-plan-case-heading">
                <span className="tap-plan-case-number">
                  {String(testCase.ordinal).padStart(2, "0")}
                </span>
                <div>
                  <h3>{readable(testCase.title)}</h3>
                  <p>{testCase.objective}</p>
                </div>
                {testCase.critical ? (
                  <span className="tap-plan-critical">{text.critical}</span>
                ) : null}
              </div>
              {testCase.scenarios.map((scenario) => (
                <section
                  className="tap-plan-scenario"
                  key={scenario.scenarioId}
                >
                  <h4>{readable(scenario.title)}</h4>
                  <ol>
                    {scenario.steps.map((step) => (
                      <li key={step.stepId}>
                        <span className="tap-plan-keyword">{step.keyword}</span>
                        <span>{step.text}</span>
                        {step.expectedResult ? (
                          <small>
                            {text.expected}: {step.expectedResult}
                          </small>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </section>
              ))}
            </article>
          ))}
        </div>
      </section>

      <section
        className="tap-plan-detail-section"
        aria-labelledby="test-evidence-title"
      >
        <h2 id="test-evidence-title">{text.evidence}</h2>
        <div className="tap-plan-evidence-list">
          {plan.citations.map((citation, index) => (
            <article className="tap-plan-evidence" key={citation.citationId}>
              <span className="tap-plan-evidence-number">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div>
                <span className="tap-plan-evidence-origin">
                  {citation.origin === "GRAPH_EXTRACTED"
                    ? text.graphOrigin
                    : text.sourceOrigin}
                </span>
                <p>{citation.claimText}</p>
                <details>
                  <summary>{text.provenance}</summary>
                  <code>
                    {citation.sourceRevisionId} · {citation.documentRevisionId}{" "}
                    · {citation.chunkId}
                  </code>
                </details>
              </div>
            </article>
          ))}
        </div>
      </section>

      {(
        [
          [text.assumptions, plan.assumptions.map((item) => item.text)],
          [text.unknowns, plan.unknowns.map((item) => item.text)],
        ] as const
      ).map(([heading, values]) =>
        values.length > 0 ? (
          <section
            className="tap-plan-detail-section tap-plan-detail-notes"
            key={heading}
            aria-label={heading}
          >
            <h2>{heading}</h2>
            <ul>
              {values.map((value) => (
                <li key={value}>{value}</li>
              ))}
            </ul>
          </section>
        ) : null,
      )}
      {plan.coverageGaps.length > 0 ? (
        <section
          className="tap-plan-detail-section tap-plan-detail-notes"
          aria-label={text.gaps}
        >
          <h2>{text.gaps}</h2>
          <ul>
            {plan.coverageGaps.map((gap) => (
              <li key={gap.gapId}>
                <strong className="tap-plan-gap-severity">
                  {text.severity[gap.severity]}
                </strong>{" "}
                {readable(gap.requirementRef)}: {gap.reason}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}
