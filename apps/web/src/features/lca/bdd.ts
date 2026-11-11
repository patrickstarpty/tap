import type {
  BddStep,
  LcaDefinition,
  LcaRun,
  LcaStep,
  Operation,
} from "./client";
export const identifier = (prefix: string) =>
  `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
export const operationLabels: Record<Operation, string> = {
  navigate: "Open page",
  click: "Click",
  fill: "Input text",
  select: "Select option",
  assert_text: "Assert text",
  assert_text_exact: "Exact rendered text assertion",
  assert_url: "Assert URL",
  scroll: "Scroll page or container",
  scroll_into_view: "Scroll into view",
  press: "Press key",
  hover: "Hover",
  double_click: "Double click",
  right_click: "Right click",
  check: "Check",
  uncheck: "Uncheck",
  drag_drop: "Drag and drop",
  wait_for: "Wait for element",
  assert_visible: "Assert visible",
  assert_value: "Assert value",
  assert_checked: "Assert checked",
  assert_read: "Read and assert",
};
export interface ArgumentField {
  key: string;
  type: "string" | "number" | "boolean" | "target";
  default: string | number | boolean;
  choices?: string[];
  optional?: boolean;
}
export const argumentFields = (operation: Operation): ArgumentField[] => {
  switch (operation) {
    case "navigate":
    case "assert_url":
      return [{ key: "url", type: "string", default: "" }];
    case "fill":
    case "select":
    case "assert_value":
      return [{ key: "value", type: "string", default: "" }];
    case "assert_text":
    case "assert_text_exact":
      return [{ key: "text", type: "string", default: "" }];
    case "press":
      return [{ key: "key", type: "string", default: "Enter" }];
    case "scroll":
      return [
        { key: "delta_x", type: "number", default: 0 },
        { key: "delta_y", type: "number", default: 600 },
      ];
    case "drag_drop":
      return [{ key: "destination_target_id", type: "target", default: "" }];
    case "wait_for":
      return [
        {
          key: "state",
          type: "string",
          default: "visible",
          choices: ["visible", "hidden", "attached", "detached"],
        },
      ];
    case "assert_read":
      return [
        {
          key: "attribute",
          type: "string",
          default: "text",
          choices: [
            "text",
            "value",
            "checked",
            "title",
            "alt",
            "placeholder",
            "role",
            "aria-label",
            "aria-description",
            "aria-valuenow",
            "aria-valuetext",
            "aria-expanded",
            "aria-selected",
            "aria-disabled",
            "aria-pressed",
            "aria-invalid",
            "data-testid",
          ],
        },
        { key: "nonempty", type: "boolean", default: true },
        ...["equals", "contains", "pattern", "name"].map((key) => ({
          key,
          type: "string" as const,
          default: "",
          optional: true,
        })),
        { key: "positive", type: "boolean", default: true, optional: true },
      ];
    case "assert_checked":
      return [{ key: "checked", type: "boolean", default: true }];
    default:
      return [];
  }
};
export const argumentKey = (operation: Operation) =>
  argumentFields(operation)[0]?.key ?? null;
export const needsTarget = (operation: Operation) =>
  !["navigate", "assert_url", "scroll"].includes(operation);
export function groupsFor(definition: LcaDefinition): BddStep[] {
  return (
    definition.bdd_steps ?? [
      {
        id: "bdd_legacy",
        keyword: "When",
        description: "Run recorded actions",
        action_ids: definition.steps.map((s) => s.id),
      },
    ]
  );
}
export function withGroups(
  definition: LcaDefinition,
  groups: BddStep[],
): LcaDefinition {
  const actions = new Map(definition.steps.map((s) => [s.id, s]));
  const ids = groups.flatMap((g) => g.action_ids);
  if (
    ids.length !== actions.size ||
    new Set(ids).size !== ids.length ||
    ids.some((id) => !actions.has(id))
  )
    throw new Error("BDD steps must contain every action exactly once.");
  return {
    ...definition,
    bdd_steps: groups,
    steps: ids.map((id) => actions.get(id)!),
  };
}
export function moveAction(
  definition: LcaDefinition,
  actionId: string,
  destination: string,
): LcaDefinition {
  const groups = groupsFor(definition);
  if (!groups.some((g) => g.id === destination))
    throw new Error("Select a BDD step first.");
  return withGroups(
    definition,
    groups.map((g) => ({
      ...g,
      action_ids: [
        ...g.action_ids.filter((id) => id !== actionId),
        ...(g.id === destination ? [actionId] : []),
      ],
    })),
  );
}
export function newAction(
  operation: Operation,
  definition: LcaDefinition,
): LcaStep {
  const fields = argumentFields(operation);
  return {
    id: identifier("step"),
    operation,
    arguments: Object.fromEntries(
      fields
        .filter((field) => !field.optional)
        .map((field) => [
          field.key,
          field.key === "url"
            ? definition.allowed_origins[0] || ""
            : field.type === "target"
              ? definition.targets[0]?.id || ""
              : field.default,
        ]),
    ),
    ...(needsTarget(operation)
      ? { target_id: definition.targets[0]?.id ?? null }
      : {}),
    timeout_ms: 10000,
    provenance: { source: "manual" },
  };
}
export function actionSummary(
  step: LcaStep,
  definition: LcaDefinition,
): string {
  const target = definition.targets.find((t) => t.id === step.target_id);
  const values = argumentFields(step.operation).map(({ key }) => {
    const argument = step.arguments[key];
    if (typeof argument === "object") return `$${argument.parameter}`;
    if (key === "destination_target_id")
      return `→ ${definition.targets.find((t) => t.id === argument)?.name || argument}`;
    return argument === undefined ? "" : String(argument);
  });
  return (
    [
      target?.name ||
        target?.value ||
        (step.operation === "scroll" ? "Page" : ""),
      ...values,
    ]
      .filter((v) => v !== "" && v !== undefined)
      .join(" · ") || "Configure action"
  );
}
export function mergeRecording(
  current: LcaDefinition,
  recorded: LcaDefinition,
  destination: string,
): LcaDefinition {
  const groups = groupsFor(current);
  if (!groups.some((g) => g.id === destination))
    throw new Error(
      "The recording destination no longer exists. Select a BDD step.",
    );
  if (current.steps.length + recorded.steps.length > 100)
    throw new Error(
      "A flow supports at most 100 actions. Split this recording before importing.",
    );
  const targets = [...current.targets],
    targetMap = new Map<string, string>();
  // Frame references are one level deep; map page targets before their children.
  for (const original of [...recorded.targets].sort(
    (a, b) => Number(!!a.frame_target_id) - Number(!!b.frame_target_id),
  )) {
    const target = original.frame_target_id
      ? {
          ...original,
          frame_target_id: targetMap.get(original.frame_target_id),
        }
      : original;
    const same = targets.find(
      (t) =>
        t.kind === target.kind &&
        t.value === target.value &&
        t.name === target.name &&
        (t.frame_target_id || null) === (target.frame_target_id || null) &&
        !!t.review_required === !!target.review_required,
    );
    if (same) {
      targetMap.set(target.id, same.id);
      continue;
    }
    const id = targets.some((t) => t.id === target.id)
      ? identifier("target")
      : target.id;
    targets.push({ ...target, id });
    targetMap.set(target.id, id);
  }
  const parameters = [...current.parameters];
  for (const parameter of recorded.parameters) {
    const existing = parameters.find((p) => p.name === parameter.name);
    if (existing && JSON.stringify(existing) !== JSON.stringify(parameter))
      throw new Error(
        `Recorded parameter ${parameter.name} conflicts with this flow.`,
      );
    if (!existing) parameters.push(parameter);
  }
  const ids = new Set(current.steps.map((s) => s.id));
  const added = recorded.steps.map((step) => {
    const id = ids.has(step.id) ? identifier("step") : step.id;
    ids.add(id);
    return {
      ...step,
      id,
      ...(step.operation === "drag_drop" &&
      typeof step.arguments.destination_target_id === "string"
        ? {
            arguments: {
              ...step.arguments,
              destination_target_id:
                targetMap.get(step.arguments.destination_target_id) ||
                step.arguments.destination_target_id,
            },
          }
        : {}),
      ...(step.target_id ? { target_id: targetMap.get(step.target_id) } : {}),
    };
  });
  const origins = [
    ...new Set([...current.allowed_origins, ...recorded.allowed_origins]),
  ];
  if (targets.length > 100 || parameters.length > 100 || origins.length > 8)
    throw new Error(
      "Recording exceeds the flow's target, parameter, or origin limits.",
    );
  return withGroups(
    {
      ...current,
      steps: [...current.steps, ...added],
      targets,
      parameters,
      allowed_origins: origins,
    },
    groups.map((g) =>
      g.id === destination
        ? { ...g, action_ids: [...g.action_ids, ...added.map((s) => s.id)] }
        : g,
    ),
  );
}
export function groupStatus(
  ids: string[],
  results: NonNullable<LcaRun["canonical_results"]>,
): string {
  if (!ids.length) return "empty";
  const statuses = ids.map(
    (id) => results.find((r) => r.step_id === id)?.status ?? "not_run",
  );
  return statuses.includes("failed")
    ? "failed"
    : statuses.every((s) => s === "passed")
      ? "passed"
      : "not_run";
}
