import type {
  WorkflowEventEnvelope,
  WorkflowTaskSnapshot,
} from "../api/client";

type Step = {
  id: string;
  keyword: string;
  title: string;
  expected_result?: string;
};
const terminal = ["succeeded", "failed", "canceled", "skipped"];
export function ActionDetails({ event }: { event: WorkflowEventEnvelope }) {
  const fields = [
    ["Action", "operation"],
    ["Page", "url"],
    ["Locator", "selector"],
    ["Input", "input"],
    ["Result", "outcome"],
    ["Error", "error"],
    ["Observation", "summary"],
    ["Reason", "reason"],
  ];
  return (
    <div className="tap-action-details">
      <dl>
        {fields.map(([label, key]) =>
          typeof event.payload[key] === "string" ? (
            <div key={key}>
              <dt>{label}</dt>
              <dd>{event.payload[key] as string}</dd>
            </div>
          ) : null,
        )}
        {typeof event.payload.duration_ms === "number" ? (
          <div>
            <dt>Duration</dt>
            <dd>{event.payload.duration_ms} ms</dd>
          </div>
        ) : null}
      </dl>
      {event.occurredAt ? (
        <time dateTime={event.occurredAt}>
          {new Date(event.occurredAt).toLocaleString()}
        </time>
      ) : null}
    </div>
  );
}
export function ExecutionSteps({
  snapshot,
}: {
  snapshot: WorkflowTaskSnapshot;
}) {
  const plan = [...snapshot.events]
    .reverse()
    .find((event) => event.kind === "browser.plan_created");
  const raw = plan?.payload.steps;
  const steps = Array.isArray(raw)
    ? raw.filter(
        (item): item is Step =>
          item !== null &&
          typeof item === "object" &&
          typeof item.id === "string" &&
          typeof item.title === "string" &&
          ["Given", "When", "Then", "And"].includes(item.keyword),
      )
    : [];
  if (!steps.length) return null;
  return (
    <section className="tap-execution-plan" aria-label="Execution plan">
      <h3>Plan</h3>
      <ol>
        {steps.map((step) => {
          const actions = snapshot.events.filter(
            (event) =>
              event.payload.step_id === step.id &&
              event.kind.startsWith("browser.action_"),
          );
          const last = actions.at(-1);
          const receipt = snapshot.browserRun?.steps.find(
            (item) => item.title === `${step.keyword} ${step.title}`,
          );
          const status = receipt
            ? receipt.status === "passed"
              ? "Passed"
              : receipt.status === "failed"
                ? "Failed"
                : receipt.status
            : last?.kind === "browser.action_started"
              ? terminal.includes(snapshot.status)
                ? "Interrupted"
                : "In progress"
              : last?.payload.outcome === "failed"
                ? "Needs attention"
                : last
                  ? "Observed · awaiting verification"
                  : terminal.includes(snapshot.status)
                    ? "Not run"
                    : "Pending";
          const finished = new Set(
            actions
              .filter((event) => event.kind === "browser.action_finished")
              .map((event) => event.payload.action_id),
          );
          const visible = actions.filter(
            (event) =>
              event.kind === "browser.action_finished" ||
              !finished.has(event.payload.action_id),
          );
          return (
            <li key={step.id}>
              <details>
                <summary>
                  <span className="tap-step-keyword">{step.keyword}</span>
                  <span>{step.title}</span>
                  <small data-state={status}>{status}</small>
                </summary>
                {step.expected_result ? (
                  <p className="tap-step-expected">
                    Expected: {step.expected_result}
                  </p>
                ) : null}
                {receipt ? (
                  <p>
                    Execution: {receipt.status} · {receipt.durationMs} ms
                  </p>
                ) : null}
                {visible.length ? (
                  visible.map((event) => (
                    <ActionDetails key={event.eventId} event={event} />
                  ))
                ) : (
                  <p>No actions yet.</p>
                )}
              </details>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
