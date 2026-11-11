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
  assert_url: "Assert URL",
};
export const argumentKey = (operation: Operation) =>
  operation === "click"
    ? null
    : operation === "fill" || operation === "select"
      ? "value"
      : operation === "assert_text"
        ? "text"
        : "url";
export const needsTarget = (operation: Operation) =>
  operation !== "navigate" && operation !== "assert_url";
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
  const key = argumentKey(operation);
  return {
    id: identifier("step"),
    operation,
    arguments: key
      ? { [key]: key === "url" ? definition.allowed_origins[0] || "" : "" }
      : {},
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
  const key = argumentKey(step.operation),
    argument = key ? step.arguments[key] : undefined;
  const value =
    typeof argument === "object" ? `$${argument.parameter}` : argument;
  return (
    [target?.name || target?.value, value].filter(Boolean).join(" · ") ||
    "Configure action"
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
  for (const target of recorded.targets) {
    const same = targets.find(
      (t) =>
        t.kind === target.kind &&
        t.value === target.value &&
        t.name === target.name &&
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
