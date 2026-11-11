import { useState } from "react";
import type {
  LcaDefinition,
  LcaParameter,
  LcaStep,
  Operation,
  Scalar,
} from "./client";

const operations: Operation[] = [
  "navigate",
  "click",
  "fill",
  "select",
  "assert_text",
  "assert_url",
];
const argumentKey = (operation: Operation) =>
  operation === "click"
    ? null
    : operation === "fill" || operation === "select"
      ? "value"
      : operation === "assert_text"
        ? "text"
        : "url";
const needsTarget = (operation: Operation) =>
  operation !== "navigate" && operation !== "assert_url";
const identifier = (prefix: string) =>
  `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;

export function parseParameterValue(
  type: LcaParameter["type"],
  value: string,
): Scalar {
  if (type === "string") return value;
  if (type === "boolean") {
    if (value === "true" || value === "false") return value === "true";
    throw new Error("Boolean values must be true or false.");
  }
  const number = Number(value);
  if (
    !value.trim() ||
    !Number.isFinite(number) ||
    (type === "integer" && !Number.isInteger(number))
  )
    throw new Error(`Enter a valid ${type} value.`);
  return number;
}

export function DefinitionEditor({
  value,
  onChange,
  disabled = false,
}: {
  value: LcaDefinition;
  onChange(value: LcaDefinition): void;
  disabled?: boolean;
}) {
  const changeStep = (index: number, next: LcaStep) =>
    onChange({
      ...value,
      steps: value.steps.map((step, i) => (i === index ? next : step)),
    });
  const move = (index: number, delta: number) => {
    const steps = [...value.steps];
    [steps[index], steps[index + delta]] = [steps[index + delta], steps[index]];
    onChange({ ...value, steps });
  };
  const add = (operation: Operation) =>
    onChange({
      ...value,
      steps: [
        ...value.steps,
        {
          id: identifier("step"),
          operation,
          arguments: { url: value.allowed_origins[0] || "" },
          timeout_ms: 10000,
          provenance: { source: "manual" },
        },
      ],
    });
  return (
    <fieldset className="lca-definition" disabled={disabled}>
      <legend>Canonical automated test steps</legend>
      <label>
        Allowed origins (one per line)
        <textarea
          value={value.allowed_origins.join("\n")}
          onChange={(e) =>
            onChange({ ...value, allowed_origins: e.target.value.split("\n") })
          }
        />
      </label>
      <p className="lca-muted">
        Only owned, non-secret test data. Credentials, uploads, downloads,
        popups and iframe recording are not supported.
      </p>
      <details>
        <summary>Targets ({value.targets.length})</summary>
        {value.targets.map((target, index) => (
          <fieldset key={target.id}>
            <legend>Target {target.id}</legend>
            <label>
              Locator strategy
              <select
                value={target.kind}
                onChange={(e) => {
                  const kind = e.target.value as typeof target.kind;
                  onChange({
                    ...value,
                    targets: value.targets.map((t, i) =>
                      i === index
                        ? {
                            id: t.id,
                            kind,
                            value: "",
                            ...(kind === "role" ? { name: "" } : {}),
                            review_required: kind === "css",
                          }
                        : t,
                    ),
                  });
                }}
              >
                <option value="test_id">Test ID</option>
                <option value="role">Role and accessible name</option>
                <option value="css">CSS fallback (review required)</option>
              </select>
            </label>
            <label>
              Locator value
              <input
                value={target.value}
                onChange={(e) =>
                  onChange({
                    ...value,
                    targets: value.targets.map((t, i) =>
                      i === index ? { ...t, value: e.target.value } : t,
                    ),
                  })
                }
              />
            </label>
            {target.kind === "role" && (
              <label>
                Accessible name
                <input
                  value={target.name || ""}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      targets: value.targets.map((t, i) =>
                        i === index ? { ...t, name: e.target.value } : t,
                      ),
                    })
                  }
                />
              </label>
            )}
            {target.kind === "css" && (
              <p>
                CSS fallback requires review. Replay never silently repairs it.
              </p>
            )}
            <button
              type="button"
              disabled={value.steps.some((s) => s.target_id === target.id)}
              onClick={() =>
                onChange({
                  ...value,
                  targets: value.targets.filter((_, i) => i !== index),
                })
              }
            >
              Remove target {target.id}
            </button>
          </fieldset>
        ))}
        <button
          type="button"
          disabled={value.targets.length >= 100}
          onClick={() =>
            onChange({
              ...value,
              targets: [
                ...value.targets,
                { id: identifier("target"), kind: "test_id", value: "" },
              ],
            })
          }
        >
          Add target
        </button>
      </details>
      <details>
        <summary>Parameters ({value.parameters.length})</summary>
        {value.parameters.map((parameter, index) => (
          <fieldset key={index}>
            <legend>Parameter {index + 1}</legend>
            <label>
              Parameter name
              <input
                value={parameter.name}
                onChange={(e) =>
                  onChange({
                    ...value,
                    parameters: value.parameters.map((p, i) =>
                      i === index ? { ...p, name: e.target.value } : p,
                    ),
                  })
                }
              />
            </label>
            <label>
              Parameter type
              <select
                value={parameter.type}
                onChange={(e) =>
                  onChange({
                    ...value,
                    parameters: value.parameters.map((p, i) =>
                      i === index
                        ? {
                            name: p.name,
                            type: e.target.value as LcaParameter["type"],
                            required: p.required,
                          }
                        : p,
                    ),
                  })
                }
              >
                {["string", "integer", "number", "boolean"].map((kind) => (
                  <option key={kind}>{kind}</option>
                ))}
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={parameter.required}
                onChange={(e) =>
                  onChange({
                    ...value,
                    parameters: value.parameters.map((p, i) =>
                      i === index ? { ...p, required: e.target.checked } : p,
                    ),
                  })
                }
              />
              Required
            </label>
            <ParameterDefault
              key={`${parameter.name}:${parameter.type}:${JSON.stringify(parameter.default)}`}
              parameter={parameter}
              onApply={(next) =>
                onChange({
                  ...value,
                  parameters: value.parameters.map((p, i) =>
                    i === index ? { ...p, default: next } : p,
                  ),
                })
              }
            />
            {parameter.default !== undefined && (
              <p>
                Saved default: {String(parameter.default)}{" "}
                <button
                  type="button"
                  onClick={() =>
                    onChange({
                      ...value,
                      parameters: value.parameters.map((p, i) =>
                        i === index
                          ? { name: p.name, type: p.type, required: p.required }
                          : p,
                      ),
                    })
                  }
                >
                  Remove default
                </button>
              </p>
            )}
            <button
              type="button"
              disabled={value.steps.some((s) =>
                Object.values(s.arguments).some(
                  (a) =>
                    typeof a === "object" && a.parameter === parameter.name,
                ),
              )}
              onClick={() =>
                onChange({
                  ...value,
                  parameters: value.parameters.filter((_, i) => i !== index),
                })
              }
            >
              Remove parameter {index + 1}
            </button>
          </fieldset>
        ))}
        <button
          type="button"
          disabled={value.parameters.length >= 100}
          onClick={() =>
            onChange({
              ...value,
              parameters: [
                ...value.parameters,
                { name: identifier("input"), type: "string", required: true },
              ],
            })
          }
        >
          Add parameter
        </button>
        <p className="lca-muted">
          Operation arguments accept string parameters. Numeric and boolean
          inputs retain their types at replay.
        </p>
      </details>
      <ol className="lca-steps">
        {value.steps.map((step, index) => {
          const key = argumentKey(step.operation);
          const argument = key ? step.arguments[key] : undefined;
          return (
            <li key={step.id}>
              <fieldset>
                <legend>
                  {index + 1}. {step.id}
                </legend>
                <div className="lca-fields">
                  <label>
                    Operation
                    <select
                      aria-label={`Step ${step.id} operation`}
                      value={step.operation}
                      onChange={(e) => {
                        const operation = e.target.value as Operation;
                        const arg = argumentKey(operation);
                        changeStep(index, {
                          ...step,
                          operation,
                          arguments: arg ? { [arg]: "" } : {},
                          target_id: needsTarget(operation)
                            ? (value.targets[0]?.id ?? null)
                            : null,
                        });
                      }}
                    >
                      {operations.map((operation) => (
                        <option key={operation} value={operation}>
                          {operation.replaceAll("_", " ")}
                        </option>
                      ))}
                    </select>
                  </label>
                  {needsTarget(step.operation) && (
                    <label>
                      Target
                      <select
                        aria-label={`Step ${step.id} target`}
                        value={step.target_id || ""}
                        onChange={(e) =>
                          changeStep(index, {
                            ...step,
                            target_id: e.target.value || null,
                          })
                        }
                      >
                        <option value="">Choose a target</option>
                        {value.targets.map((target) => (
                          <option key={target.id} value={target.id}>
                            {target.id}: {target.value}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {key && (
                    <>
                      <label>
                        Value source
                        <select
                          aria-label={`Step ${step.id} value source`}
                          value={
                            typeof argument === "object"
                              ? "parameter"
                              : "literal"
                          }
                          onChange={(e) =>
                            changeStep(index, {
                              ...step,
                              arguments: {
                                [key]:
                                  e.target.value === "parameter"
                                    ? {
                                        parameter:
                                          value.parameters.find(
                                            (p) => p.type === "string",
                                          )?.name || "",
                                      }
                                    : "",
                              },
                            })
                          }
                        >
                          <option value="literal">Literal</option>
                          <option value="parameter">String parameter</option>
                        </select>
                      </label>
                      <label>
                        {key}
                        {typeof argument === "object" ? (
                          <select
                            aria-label={`Step ${step.id} parameter`}
                            value={argument.parameter}
                            onChange={(e) =>
                              changeStep(index, {
                                ...step,
                                arguments: {
                                  [key]: { parameter: e.target.value },
                                },
                              })
                            }
                          >
                            <option value="">Choose a parameter</option>
                            {value.parameters
                              .filter((p) => p.type === "string")
                              .map((p) => (
                                <option key={p.name}>{p.name}</option>
                              ))}
                          </select>
                        ) : (
                          <input
                            aria-label={`Step ${step.id} value`}
                            value={argument || ""}
                            onChange={(e) =>
                              changeStep(index, {
                                ...step,
                                arguments: { [key]: e.target.value },
                              })
                            }
                          />
                        )}
                      </label>
                    </>
                  )}
                  <label>
                    Timeout (ms)
                    <input
                      aria-label={`Step ${step.id} timeout`}
                      type="number"
                      min={1}
                      max={30000}
                      step={1}
                      value={step.timeout_ms}
                      onChange={(e) =>
                        changeStep(index, {
                          ...step,
                          timeout_ms: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                </div>
                <div className="lca-actions">
                  <button
                    type="button"
                    aria-label={`Move ${step.id} up`}
                    disabled={index === 0}
                    onClick={() => move(index, -1)}
                  >
                    Move up
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${step.id} down`}
                    disabled={index === value.steps.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    Move down
                  </button>
                  <button
                    type="button"
                    disabled={value.steps.length === 1}
                    onClick={() =>
                      onChange({
                        ...value,
                        steps: value.steps.filter((_, i) => i !== index),
                      })
                    }
                  >
                    Remove {step.id}
                  </button>
                </div>
              </fieldset>
            </li>
          );
        })}
      </ol>
      <div className="lca-actions">
        <button
          type="button"
          disabled={value.steps.length >= 100}
          onClick={() => add("navigate")}
        >
          Add step
        </button>
        <button
          type="button"
          disabled={value.steps.length >= 100}
          onClick={() => add("assert_url")}
        >
          Add assertion
        </button>
      </div>
      <p className="lca-muted">
        Assertions are explicit checks. Successful interactions alone do not
        prove a business result.
      </p>
      <details>
        <summary>Canonical definition (read-only)</summary>
        <pre>{JSON.stringify(value, null, 2)}</pre>
      </details>
    </fieldset>
  );
}

function ParameterDefault({
  parameter,
  onApply,
}: {
  parameter: LcaParameter;
  onApply(value: Scalar): void;
}) {
  const [draft, setDraft] = useState(
    parameter.default === undefined ? "" : String(parameter.default),
  );
  const [error, setError] = useState("");
  return (
    <div>
      <label>
        Default for {parameter.name}
        <input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value);
            setError("");
          }}
        />
      </label>
      <button
        type="button"
        onClick={() => {
          try {
            const value = parseParameterValue(parameter.type, draft);
            setError("");
            onApply(value);
          } catch (cause) {
            setError(
              cause instanceof Error ? cause.message : "Invalid default.",
            );
          }
        }}
      >
        Apply default for {parameter.name}
      </button>
      <p className="lca-muted">
        Apply to include this value in the draft. Blank text is an explicit
        empty string for string parameters.
      </p>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
