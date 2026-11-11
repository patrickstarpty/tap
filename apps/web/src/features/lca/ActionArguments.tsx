import { useState } from "react";
import type { LcaDefinition, LcaStep, Scalar } from "./client";
import type { ArgumentField } from "./bdd";

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
  return (
    <>
      {fields.map((field) => {
        const argument = step.arguments[field.key];
        const parameter = typeof argument === "object";
        const prefix = `Step ${step.id}${fields.length > 1 ? ` ${field.key}` : ""}`;
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
            <label>
              {field.key}
              {parameter ? (
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
              ) : step.operation === "assert_text_exact" ? (
                <textarea
                  aria-label={`${prefix} value`}
                  value={String(argument ?? "")}
                  onChange={(e) => update(field.key, e.target.value)}
                />
              ) : (
                <input
                  aria-label={`${prefix} value`}
                  type="text"
                  inputMode={field.type === "number" ? "decimal" : undefined}
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
          </div>
        );
      })}
      {invalidNumbers.map((field) => (
        <p role="alert" key={field.key}>
          {field.key}: Scroll distance must be a number between -10000 and
          10000.
        </p>
      ))}
    </>
  );
}
