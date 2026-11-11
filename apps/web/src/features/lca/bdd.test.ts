import { expect, it } from "vitest";
import type { LcaDefinition } from "./client";
import {
  newAction,
  actionSummary,
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

it("creates typed defaults and summaries for common browser actions", () => {
  expect(newAction("scroll", d)).toMatchObject({
    arguments: { delta_x: 0, delta_y: 600 },
  });
  expect(newAction("scroll", d).target_id).toBeUndefined();
  expect(newAction("press", d).arguments).toEqual({ key: "Enter" });
  expect(newAction("wait_for", d).arguments).toEqual({ state: "visible" });
  expect(newAction("assert_checked", d).arguments).toEqual({ checked: true });
  expect(
    actionSummary(
      { ...newAction("assert_checked", d), arguments: { checked: false } },
      d,
    ),
  ).toContain("false");
});

it("remaps frame and drag destination references when importing colliding targets", () => {
  const current: LcaDefinition = {
    ...d,
    targets: [{ id: "frame", kind: "test_id", value: "old" }],
  };
  const recorded: LcaDefinition = {
    ...d,
    targets: [
      { id: "item", kind: "test_id", value: "item", frame_target_id: "frame" },
      { id: "frame", kind: "test_id", value: "new-frame" },
      { id: "drop", kind: "test_id", value: "drop", frame_target_id: "frame" },
    ],
    steps: [
      {
        id: "drag",
        operation: "drag_drop",
        target_id: "item",
        arguments: { destination_target_id: "drop" },
        timeout_ms: 10000,
      },
    ],
  };
  const next = mergeRecording(current, recorded, "bdd_legacy");
  const frame = next.targets.find((t) => t.value === "new-frame")!;
  expect(frame.id).not.toBe("frame");
  expect(next.targets.find((t) => t.value === "item")?.frame_target_id).toBe(
    frame.id,
  );
  expect(next.steps[1].arguments.destination_target_id).toBe(
    next.targets.find((t) => t.value === "drop")?.id,
  );
});

it("keeps exact rendered text assertions distinct from normalized text assertions", () => {
  const exact = newAction("assert_text_exact", d);
  expect(exact.arguments).toEqual({ text: "" });
  expect(exact.target_id).toBeNull();
  expect(
    actionSummary({ ...exact, arguments: { text: "  exact\n text  " } }, d),
  ).toBe("  exact\n text  ");
});

it("remaps imported page identities, explicit bindings and popup destinations without changing existing actions", () => {
  const current: LcaDefinition = {
    ...d,
    steps: [
      ...d.steps,
      {
        id: "existing_page",
        operation: "open_page",
        arguments: { page_id: "details", url: "https://example.com/old" },
        timeout_ms: 10000,
      },
    ],
  };
  const recorded: LcaDefinition = {
    ...d,
    targets: [{ id: "launch", kind: "test_id", value: "launch" }],
    steps: [
      {
        id: "popup",
        operation: "click_popup",
        target_id: "launch",
        arguments: { page_id: "details" },
        timeout_ms: 10000,
      },
      {
        id: "switch",
        operation: "switch_page",
        arguments: { page_id: "details" },
        timeout_ms: 10000,
      },
      {
        id: "read",
        operation: "assert_url",
        page_id: "details",
        arguments: { url: "https://example.com/new" },
        timeout_ms: 10000,
      },
    ],
  };
  const merged = mergeRecording(current, recorded, "bdd_legacy");
  const destination = merged.steps[2].arguments.page_id;
  expect(destination).not.toBe("details");
  expect(merged.steps[3].arguments.page_id).toBe(destination);
  expect(merged.steps[4].page_id).toBe(destination);
  expect(merged.steps.slice(0, 2)).toEqual(current.steps);
  expect(recorded.steps[0].arguments.page_id).toBe("details");
});

it("binds imported implicit main actions instead of targeting the insertion's active popup", () => {
  const current: LcaDefinition = {
    ...d,
    steps: [
      {
        id: "existing_page",
        operation: "open_page",
        arguments: { page_id: "details", url: "https://example.com" },
        timeout_ms: 10000,
      },
      {
        id: "existing_switch",
        operation: "switch_page",
        arguments: { page_id: "details" },
        timeout_ms: 10000,
      },
    ],
  };
  const merged = mergeRecording(current, d, "bdd_legacy");
  expect(merged.steps[2].page_id).toBe("main");
  expect(merged.steps.slice(0, 2)).toEqual(current.steps);
});

it("rejects imports that change the page of a later existing implicit action", () => {
  const current: LcaDefinition = {
    ...d,
    steps: [...d.steps, { ...d.steps[0], id: "later" }],
    bdd_steps: [
      {
        id: "first",
        keyword: "Given",
        description: "Open",
        action_ids: ["open"],
      },
      {
        id: "second",
        keyword: "When",
        description: "Continue",
        action_ids: ["later"],
      },
    ],
  };
  const recorded: LcaDefinition = {
    ...d,
    steps: [
      {
        id: "new_page",
        operation: "open_page",
        arguments: { page_id: "details", url: "https://example.com" },
        timeout_ms: 10000,
      },
      {
        id: "new_switch",
        operation: "switch_page",
        arguments: { page_id: "details" },
        timeout_ms: 10000,
      },
    ],
  };
  expect(() => mergeRecording(current, recorded, "first")).toThrow(
    /change.*page.*later/i,
  );
  expect(current.steps[1].page_id).toBeUndefined();
});

it("rejects importing actions that refer to a page already closed by the existing flow", () => {
  const current: LcaDefinition = {
    ...d,
    steps: [
      {
        id: "new_page",
        operation: "open_page",
        arguments: { page_id: "details", url: "https://example.com" },
        timeout_ms: 10000,
      },
      {
        id: "new_switch",
        operation: "switch_page",
        arguments: { page_id: "details" },
        timeout_ms: 10000,
      },
      {
        id: "close_main",
        operation: "close_page",
        arguments: { page_id: "main" },
        timeout_ms: 10000,
      },
    ],
  };
  expect(() => mergeRecording(current, d, "bdd_legacy")).toThrow(/not open/);
});
