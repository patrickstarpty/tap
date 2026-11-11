import type { LcaRun } from "./client";
import { groupStatus, operationLabels } from "./bdd";
export function BddResults({ run }: { run: LcaRun }) {
  const definition = run.definition_snapshot?.definition;
  if (!definition?.bdd_steps || !run.canonical_results) return null;
  const results = run.canonical_results;
  return (
    <section aria-label="BDD results">
      <h4>Business step results</h4>
      {definition.bdd_steps.map((group) => {
        const status = groupStatus(group.action_ids, results);
        return (
          <details
            key={group.id}
            open={status === "failed"}
            className={`lca-bdd-result lca-result-${status}`}
          >
            <summary>
              {group.keyword} {group.description} ·{" "}
              {status.replaceAll("_", " ")}
            </summary>
            <ol>
              {group.action_ids.map((id) => {
                const step = definition.steps.find((s) => s.id === id);
                return (
                  <li key={id}>
                    {step ? operationLabels[step.operation] : "Action"} · {id} ·{" "}
                    {results.find((r) => r.step_id === id)?.status ?? "not_run"}
                  </li>
                );
              })}
            </ol>
          </details>
        );
      })}
    </section>
  );
}
