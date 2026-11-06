import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import type { WorkflowEventEnvelope } from "../api/client";
import { ActivityTimeline } from "./ActivityTimeline";

it("names actual public tool and Skill steps without rendering private payload", () => {
  const events = [
    [
      "node.tool_started",
      { tool_id: "knowledge.search", secret: "private-query" },
    ],
    [
      "node.tool_finished",
      { tool_id: "knowledge.search", outcome: "succeeded" },
    ],
    ["node.skill_started", { skill_id: "citation-skill" }],
    [
      "node.skill_finished",
      { skill_id: "citation-skill", outcome: "activated" },
    ],
  ].map(([kind, payload], index) => ({
    eventId: `event-${index}`,
    taskId: "task-1",
    sequence: index + 1,
    nodeId: "node-1",
    attempt: 1,
    kind,
    payload,
    occurredAt: "2026-11-06T00:00:00Z",
    status: "running",
  })) as WorkflowEventEnvelope[];
  render(<ActivityTimeline events={events} />);
  expect(screen.getByText("Tool started: knowledge.search")).toBeVisible();
  expect(screen.getByText("Tool succeeded: knowledge.search")).toBeVisible();
  expect(screen.getByText("Skill started: citation-skill")).toBeVisible();
  expect(screen.getByText("Skill activated: citation-skill")).toBeVisible();
  expect(screen.queryByText("private-query")).toBeNull();
});
