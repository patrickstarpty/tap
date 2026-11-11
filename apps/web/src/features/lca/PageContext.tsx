import type { LcaDefinition, LcaStep } from "./client";
import {
  pageContextBefore,
  pageContextError,
  pageLifecycle,
} from "./pageBindings";

export function PageContext({
  step,
  definition,
  onChange,
}: {
  step: LcaStep;
  definition: LcaDefinition;
  onChange(step: LcaStep): void;
}) {
  const context = pageContextBefore(definition, step.id);
  const error = pageContextError(definition, step);
  return (
    <div className="lca-page-context">
      {!pageLifecycle(step) && (
        <>
          <label>
            Page context
            <select
              aria-label={`Step ${step.id} page context`}
              value={step.page_id ?? ""}
              onChange={(event) => {
                const next = { ...step };
                if (event.target.value) next.page_id = event.target.value;
                else delete next.page_id;
                onChange(next);
              }}
            >
              <option value="">Active page ({context.active})</option>
              {[...context.open].map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
              {step.page_id && !context.open.has(step.page_id) && (
                <option value={step.page_id}>
                  {step.page_id} (unavailable)
                </option>
              )}
            </select>
          </label>
          <p className="lca-muted">
            Selected page: {step.page_id ?? context.active}
          </p>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
