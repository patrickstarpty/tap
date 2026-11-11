import { expect, it } from "vitest";
import type { LcaDefinition } from "./client";
import {
  groupsFor,
  withGroups,
  moveAction,
  mergeRecording,
  groupStatus,
} from "./bdd";
const d: LcaDefinition = {
  schema_version: "lca-debug-v1",
  allowed_origins: ["https://example.com"],
  targets: [],
  parameters: [],
  steps: [
    {
      id: "open",
      operation: "navigate",
      arguments: { url: "https://example.com" },
      timeout_ms: 10000,
    },
  ],
};
it("views legacy actions without mutating the definition", () => {
  const before = JSON.stringify(d);
  expect(groupsFor(d)[0].action_ids).toEqual(["open"]);
  expect(JSON.stringify(d)).toBe(before);
  expect(d.bdd_steps).toBeUndefined();
});
it("group ordering drives canonical action ordering", () => {
  const next = withGroups(
    { ...d, steps: [...d.steps, { ...d.steps[0], id: "end" }] },
    [
      {
        id: "second",
        keyword: "Then",
        description: "See result",
        action_ids: ["end"],
      },
      {
        id: "first",
        keyword: "Given",
        description: "Open",
        action_ids: ["open"],
      },
    ],
  );
  expect(next.steps.map((s) => s.id)).toEqual(["end", "open"]);
});
it("moves actions across groups without duplicates", () => {
  const base = withGroups(d, [
    ...groupsFor(d),
    { id: "other", keyword: "Then", description: "Check", action_ids: [] },
  ]);
  const next = moveAction(base, "open", "other");
  expect(next.bdd_steps?.map((g) => g.action_ids)).toEqual([[], ["open"]]);
  expect(next.steps).toHaveLength(1);
});
it("appends recording without replacing other actions and renames collisions", () => {
  const next = mergeRecording(d, d, groupsFor(d)[0].id);
  expect(next.steps).toHaveLength(2);
  expect(new Set(next.steps.map((s) => s.id)).size).toBe(2);
  expect(next.bdd_steps?.[0].action_ids).toEqual(next.steps.map((s) => s.id));
  expect(next.steps[0]).toEqual(d.steps[0]);
});
it("fails closed when import target disappeared or limits are exceeded", () => {
  expect(() => mergeRecording(d, d, "gone")).toThrow();
  expect(() =>
    mergeRecording(
      {
        ...d,
        steps: Array.from({ length: 100 }, (_, i) => ({
          ...d.steps[0],
          id: `s${i}`,
        })),
      },
      d,
      "bdd_legacy",
    ),
  ).toThrow(/100/);
});
it("summarizes only fully passed groups as passed", () => {
  expect(groupStatus(["a", "b"], [{ step_id: "a", status: "passed" }])).toBe(
    "not_run",
  );
  expect(
    groupStatus(
      ["a", "b"],
      [
        { step_id: "a", status: "passed" },
        { step_id: "b", status: "failed" },
      ],
    ),
  ).toBe("failed");
  expect(groupStatus([], [])).toBe("empty");
});
it("remaps target collisions while preserving existing target bindings", () => {
  const current: LcaDefinition = {
    ...d,
    targets: [{ id: "button", kind: "test_id", value: "old" }],
  };
  const recorded: LcaDefinition = {
    ...d,
    targets: [{ id: "button", kind: "test_id", value: "new" }],
    steps: [
      {
        id: "click",
        operation: "click",
        arguments: {},
        target_id: "button",
        timeout_ms: 10000,
      },
    ],
  };
  const next = mergeRecording(current, recorded, "bdd_legacy");
  expect(next.targets[0]).toEqual(current.targets[0]);
  expect(next.targets[1].id).not.toBe("button");
  expect(next.steps[1].target_id).toBe(next.targets[1].id);
  expect(recorded.steps[0].target_id).toBe("button");
});
it("refuses conflicting parameter definitions instead of changing existing data", () => {
  const current: LcaDefinition = {
    ...d,
    parameters: [{ name: "product", type: "string", required: true }],
  };
  const recorded: LcaDefinition = {
    ...d,
    parameters: [{ name: "product", type: "string", required: false }],
  };
  expect(() => mergeRecording(current, recorded, "bdd_legacy")).toThrow(
    /conflicts/,
  );
  expect(current.parameters[0].required).toBe(true);
});
