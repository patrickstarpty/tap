import type { AcceptanceContract } from "./client";

/** Source evidence stays read-only; it is never approval of subsequent edits. */
export function SourceAcceptanceReview({
  status,
  contract,
}: {
  status: "reviewed" | "legacy_unreviewed" | "invalid";
  contract: AcceptanceContract | null;
}) {
  return (
    <details className="lca-recording-disclosure" open>
      <summary>
        Original AA requirements ·{" "}
        {status === "reviewed"
          ? "reviewed source"
          : status === "invalid"
            ? "invalid source"
            : "legacy / unreviewed"}
      </summary>
      <p>
        The original requirements are source evidence, not approval of this
        edited LCA flow. Only a current replay that proves every required field
        can pass requirements acceptance.
      </p>
      <p>
        To change the reviewed requirements or their action/target bindings,
        create a new AA task, review its required inputs, and import its
        successful result as a new LCA flow. Saving edits here does not update
        the original approval.
      </p>
      {status === "reviewed" && contract ? (
        <ul>
          {contract.fields.map((field) => (
            <li key={field.id}>
              <strong>{field.id}</strong>: supplied value {field.value};
              required readback {JSON.stringify(field.readback_value)} (
              {field.attribute}). Target: {field.target_texts.join(" / ")}
              {field.scope_text ? `; scope: ${field.scope_text}` : ""}.
            </li>
          ))}
        </ul>
      ) : (
        <p>
          {status === "invalid"
            ? "The frozen source contract could not be validated. Executable import is blocked."
            : "This original task has no reviewed structured requirements. Technical success does not certify free-form requirements."}
        </p>
      )}
    </details>
  );
}
