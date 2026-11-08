import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import type { WorkflowTaskSnapshot } from "../api/client";
import { ExecutionSteps } from "./ExecutionSteps";

const snapshot = {
  status: "running",
  events: [
    {
      eventId: "plan",
      sequence: 1,
      kind: "browser.plan_created",
      payload: {
        steps: [
          {
            id: "open",
            keyword: "Given",
            title: "打开产品列表",
            expected_result: "显示保险产品",
          },
          {
            id: "quote",
            keyword: "Then",
            title: "核对月付保费",
            expected_result: "保费大于零",
          },
        ],
      },
    },
  ],
} as unknown as WorkflowTaskSnapshot;
it("shows the saved plan before actions, then links failed actions without inventing a pass", async () => {
  const { rerender } = render(<ExecutionSteps snapshot={snapshot} />);
  expect(screen.getByText("打开产品列表")).toBeVisible();
  expect(screen.getAllByText("Pending")).toHaveLength(2);
  rerender(
    <ExecutionSteps
      snapshot={{
        ...snapshot,
        events: [
          ...snapshot.events,
          {
            taskId: "task-1",
            nodeId: "root",
            attempt: 1,
            eventId: "action",
            sequence: 2,
            kind: "browser.action_finished",
            occurredAt: "2026-11-08T00:00:00Z",
            payload: {
              step_id: "open",
              operation: "click",
              outcome: "failed",
              selector: "#product",
              error: "strict mode violation",
              duration_ms: 12,
              url: "https://example.test/",
            },
          } as WorkflowTaskSnapshot["events"][number],
        ],
      }}
    />,
  );
  expect(screen.getByText("Needs attention")).toBeVisible();
  await userEvent.click(screen.getByText("打开产品列表"));
  expect(screen.getByText("strict mode violation")).toBeVisible();
  expect(screen.getByText("#product")).toBeVisible();
  expect(screen.queryByText("Passed")).toBeNull();
});
it("only a persisted execution receipt can mark a BDD assertion passed", () => {
  render(
    <ExecutionSteps
      snapshot={{
        ...snapshot,
        status: "succeeded",
        browserRun: {
          steps: [
            { title: "Then 核对月付保费", status: "passed", durationMs: 21 },
          ],
        } as WorkflowTaskSnapshot["browserRun"],
      }}
    />,
  );
  expect(screen.getByText("Passed")).toBeVisible();
  expect(screen.getByText("Not run")).toBeVisible();
});
