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
      </section>
    </article>
  );
}
