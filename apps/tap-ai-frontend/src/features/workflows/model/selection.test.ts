import { expect, it } from "vitest";
import { agentSelectionFields, followUpTaskId } from "./selection";

const agents = new Map([
  ["rev-sdet", { assetId: "sdet" }],
  ["rev-review", { assetId: "reviewer" }],
]);

it("sends all stable Agent IDs only in native mode and keeps legacy exact revision single-select", () => {
  expect(
    agentSelectionFields(true, ["rev-sdet", "rev-review"], agents),
  ).toEqual({
    agentRevisionId: null,
    requestedAgentIds: ["sdet", "reviewer"],
  });
  expect(() =>
    agentSelectionFields(false, ["rev-sdet", "rev-review"], agents),
  ).toThrow("Choose one Agent");
  expect(agentSelectionFields(false, ["rev-sdet"], agents)).toEqual({
    agentRevisionId: "rev-sdet",
    requestedAgentIds: [],
  });
  expect(() => agentSelectionFields(true, ["removed-rev"], agents)).toThrow(
    "Unavailable Agent",
  );
});

it("only follows a live native task associated with this conversation", () => {
  const task = {
    taskId: "task-1",
    conversationId: "chat-1",
    origin: "native" as const,
    status: "running",
    availableActions: ["steer"],
  };
  expect(followUpTaskId(task, "chat-1")).toBe("task-1");
  expect(followUpTaskId({ ...task, status: "succeeded" }, "chat-1")).toBeNull();
  expect(followUpTaskId({ ...task, origin: "observed" }, "chat-1")).toBeNull();
  expect(followUpTaskId(task, "chat-2")).toBeNull();
});
