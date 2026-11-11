import { useState } from "react";
import type { LcaDefinition, LcaStep, Scalar } from "./client";
import type { ArgumentField } from "./bdd";
import { pageContextBefore } from "./pageBindings";

export function ActionArguments({
  step,
  definition,
  fields,
  onChange,
}: {
  step: LcaStep;
  definition: LcaDefinition;
  fields: ArgumentField[];
  onChange(step: LcaStep): void;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const invalidNumbers = fields.filter((field) => {
    const argument = step.arguments[field.key];
    return (
      field.type === "number" &&
      typeof argument !== "object" &&
      (typeof argument !== "number" ||
        !Number.isFinite(argument) ||
        Math.abs(argument) > 10000)
    );
  });
  const source = definition.targets.find(
    (target) => target.id === step.target_id,
  );
  const update = (key: string, value: Scalar | { parameter: string }) => {
    onChange({ ...step, arguments: { ...step.arguments, [key]: value } });
  };
  if (step.operation === "assert_read") {
    const toggle = (key: string, enabled: boolean, value: Scalar) => {
      const args = { ...step.arguments };
      if (enabled) args[key] = value;
      else delete args[key];
      onChange({ ...step, arguments: args });
    };
    const hasCondition = [
      "equals",
      "contains",
      "nonempty",
      "positive",
      "pattern",
    ].some((key) => key in step.arguments);
    return (
      <>
        <p>
          Read the exact DOM value. At least one condition is required. Patterns
          use a bounded subset: no groups or alternatives, at most one
          start-anchored quantifier.
        </p>
        {fields.map((field) => {
          const enabled = field.key in step.arguments;
          const optional = field.key !== "attribute";
          const label = `Step ${step.id}`;
          return (
            <div key={field.key}>
              {optional && (
                <label>
                  <input
                    type="checkbox"
                    aria-label={`${label} enable ${field.key}`}
                    checked={enabled}
                    onChange={(e) =>
                      toggle(field.key, e.target.checked, field.default)
                    }
                  />
                  {field.key}
                </label>
              )}
              {enabled && field.type !== "boolean" && (
                <label>
                  {field.key}
                  {field.choices ? (
                    <select
                      aria-label={`${label} ${field.key} value`}
                      value={String(step.arguments[field.key])}
                      onChange={(e) => update(field.key, e.target.value)}
                    >
                      {field.choices.map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </select>
                  ) : (
                    <textarea
                      aria-label={`${label} ${field.key} value`}
                      value={String(step.arguments[field.key] ?? "")}
                      onChange={(e) => update(field.key, e.target.value)}
                    />
                  )}
                </label>
              )}
            </div>
          );
        })}
        {!hasCondition && (
          <p role="alert">Read assertion requires at least one condition.</p>
        )}
      </>
    );
  }
  return (
    <>
      {fields.map((field) => {
        const argument = step.arguments[field.key];
        const parameter = typeof argument === "object";
        const prefix = `Step ${step.id}${fields.length > 1 || field.literalOnly ? ` ${field.key}` : ""}`;
        const enabled = field.key in step.arguments;
        const pageChoices =
          field.key === "page_id" &&
          ["switch_page", "close_page"].includes(step.operation)
            ? [...pageContextBefore(definition, step.id).open]
            : null;
        const parameters = definition.parameters.filter(
          (p) =>
            p.type === field.type ||
            (field.type === "number" && p.type === "integer"),
        );
        if (field.type === "target")
          return (
            <label key={field.key}>
              Destination target
              <select
                aria-label={`Step ${step.id} destination target`}
                value={String(argument ?? "")}
                onChange={(e) => update(field.key, e.target.value)}
              >
                <option value="">Choose a destination target</option>
                {definition.targets
                  .filter(
                    (t) =>
                      source &&
                      (t.frame_target_id || null) ===
                        (source.frame_target_id || null),
                  )
                  .map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name || t.value || t.id}
                    </option>
                  ))}
              </select>
            </label>
          );
        return (
          <div key={field.key}>
            {field.optional && (
              <label>
                <input
                  type="checkbox"
                  aria-label={`Step ${step.id} enable ${field.key}`}
                  checked={enabled}
                  onChange={(event) => {
                    const args = { ...step.arguments };
                    if (event.target.checked) args[field.key] = field.default;
                    else delete args[field.key];
                    onChange({ ...step, arguments: args });
                  }}
                />
                Include {field.key}
              </label>
            )}
            {(!field.optional || enabled) && (
              <>
                {!field.literalOnly && (
                  <label>
                    Value source
                    <select
                      aria-label={`${prefix} value source`}
                      value={parameter ? "parameter" : "literal"}
                      onChange={(e) => {
                        if (field.type === "number")
                          setDrafts({
                            ...drafts,
                            [field.key]: String(field.default),
                          });
                        update(
                          field.key,
                          e.target.value === "parameter"
                            ? { parameter: parameters[0]?.name || "" }
                            : field.default,
                        );
                      }}
                    >
                      <option value="literal">Literal</option>
                      <option value="parameter">
                        {field.type === "string"
                          ? "String"
                          : field.type === "number"
                            ? "Numeric"
                            : "Boolean"}{" "}
                        parameter
                      </option>
                    </select>
                  </label>
                )}
                <label>
                  {field.key}
                  {pageChoices ? (
                    <select
                      aria-label={`${prefix} value`}
                      value={String(argument ?? "")}
                      onChange={(event) =>
                        update(field.key, event.target.value)
                      }
                    >
                      <option value="">Choose an open page</option>
                      {pageChoices.map((id) => (
                        <option key={id} value={id}>
                          {id}
                        </option>
                      ))}
                      {argument && !pageChoices.includes(String(argument)) && (
                        <option value={String(argument)}>
                          {String(argument)} (unavailable)
                        </option>
                      )}
                    </select>
                  ) : parameter ? (
                    <select
                      aria-label={`${prefix} parameter`}
                      value={argument.parameter}
                      onChange={(e) =>
                        update(field.key, { parameter: e.target.value })
                      }
                    >
                      <option value="">Choose a parameter</option>
                      {parameters.map((p) => (
                        <option key={p.name}>{p.name}</option>
                      ))}
                    </select>
                  ) : field.choices || field.type === "boolean" ? (
                    <select
                      aria-label={`${prefix} value`}
                      value={String(argument ?? field.default)}
                      onChange={(e) =>
                        update(
                          field.key,
                          field.type === "boolean"
                            ? e.target.value === "true"
                            : e.target.value,
                        )
                      }
                    >
                      {(field.choices || ["true", "false"]).map((choice) => (
                        <option key={choice}>{choice}</option>
                      ))}
                    </select>
                  ) : step.operation === "assert_text_exact" ||
                    field.multiline ? (
                    <textarea
                      aria-label={`${prefix} value`}
                      value={String(argument ?? "")}
                      onChange={(e) => update(field.key, e.target.value)}
                    />
                  ) : (
                    <input
                      aria-label={`${prefix} value`}
                      type="text"
                      inputMode={
                        field.type === "number" ? "decimal" : undefined
                      }
                      min={field.type === "number" ? -10000 : undefined}
                      max={field.type === "number" ? 10000 : undefined}
                      step={field.type === "number" ? "any" : undefined}
                      value={drafts[field.key] ?? String(argument ?? "")}
                      onChange={(e) => {
                        if (field.type === "number") {
                          setDrafts({ ...drafts, [field.key]: e.target.value });
                          const number = Number(e.target.value);
                          if (
                            !e.target.value.trim() ||
                            !Number.isFinite(number) ||
                            Math.abs(number) > 10000
                          ) {
                            // Keep invalid text in the definition so save/run validation
                            // cannot silently use the previous valid distance.
                            update(field.key, e.target.value);
                            return;
                          }
                          update(field.key, number);
                        } else update(field.key, e.target.value);
                      }}
                    />
                  )}
                </label>
              </>
            )}
          </div>
        );
      })}
      {step.operation === "click_dialog" &&
        "prompt_text" in step.arguments &&
        (step.arguments.action !== "accept" ||
          step.arguments.type !== "prompt") && (
          <p role="alert">
            Prompt text requires an accepted prompt dialog. Remove prompt text
            or change the dialog action and type.
          </p>
        )}
      {step.operation === "open_page" || step.operation === "click_popup" ? (
        <p className="lca-muted">
          Create a new page ID without switching the active page. Use Switch
          page or choose an explicit page context for subsequent actions.
        </p>
      ) : null}
      {step.operation === "click_dialog" && (
        <p className="lca-muted">
          Click the target and handle exactly one dialog with this type and
          exact message. No automatic dialog acceptance.
        </p>
      )}
      {invalidNumbers.map((field) => (
        <p role="alert" key={field.key}>
          {field.key}: Scroll distance must be a number between -10000 and
          10000.
        </p>
      ))}
    </>
  );
}
