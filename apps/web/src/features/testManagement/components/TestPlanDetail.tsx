import type { TestPlanRevision } from "../api/client";

export function TestPlanDetail({ plan }: { plan: TestPlanRevision }) {
  return (
    <article aria-labelledby="test-plan-title">
      <header>
        <span>{plan.status}</span>
        <h2 id="test-plan-title">{plan.title}</h2>
        <p>{plan.objective}</p>
      </header>
      <section aria-labelledby="test-cases-title">
        <h3 id="test-cases-title">测试用例</h3>
        {plan.cases.map((testCase) => (
          <article key={testCase.caseId}>
            <h4>{testCase.title}</h4>
            <p>{testCase.objective}</p>
            {testCase.scenarios.map((scenario) => (
              <section key={scenario.scenarioId}>
                <h5>{scenario.title}</h5>
                <ol>
                  {scenario.steps.map((step) => (
                    <li key={step.stepId}>
                      <strong>{step.keyword}</strong> {step.text}
                      {step.expectedResult ? (
                        <small> — {step.expectedResult}</small>
                      ) : null}
                    </li>
                  ))}
                </ol>
              </section>
            ))}
          </article>
        ))}
      </section>
      <section aria-labelledby="test-evidence-title">
        <h3 id="test-evidence-title">依据与待确认项</h3>
        <p>
          {plan.citations.length} 条来源依据 · {plan.assumptions.length} 项假设
          · {plan.unknowns.length} 项未知 · {plan.coverageGaps.length}{" "}
          个覆盖缺口
        </p>
        {plan.citations.length > 0 ? (
          <section aria-labelledby="test-citations-title">
            <h4 id="test-citations-title">来源依据</h4>
            <ul>
              {plan.citations.map((citation) => (
                <li key={citation.citationId}>
                  <p>{citation.claimText}</p>
                  <small>
                    {citation.origin} · source {citation.sourceRevisionId} ·
                    document {citation.documentRevisionId} · chunk{" "}
                    {citation.chunkId}
                  </small>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {plan.assumptions.length > 0 ? (
          <section aria-labelledby="test-assumptions-title">
            <h4 id="test-assumptions-title">假设</h4>
            <ul>
              {plan.assumptions.map((assumption) => (
                <li key={assumption.factId}>
                  {assumption.text}
                  {assumption.graphEdgeId ? (
                    <small> · graph edge {assumption.graphEdgeId}</small>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {plan.unknowns.length > 0 ? (
          <section aria-labelledby="test-unknowns-title">
            <h4 id="test-unknowns-title">未知项</h4>
            <ul>
              {plan.unknowns.map((unknown) => (
                <li key={unknown.factId}>{unknown.text}</li>
              ))}
            </ul>
          </section>
        ) : null}
        {plan.coverageGaps.length > 0 ? (
          <section aria-labelledby="test-coverage-gaps-title">
            <h4 id="test-coverage-gaps-title">覆盖缺口</h4>
            <ul>
              {plan.coverageGaps.map((gap) => (
                <li key={gap.gapId}>
                  <strong>{gap.severity}</strong> · {gap.requirementRef}:{" "}
                  {gap.reason}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </section>
    </article>
  );
}
