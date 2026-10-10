import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { AnswerActivity, AnswerProgress } from "./TapperWorkspace";

it("shows the answer summary line and expands event details on click", async () => {
  render(
    <AnswerActivity
      locale="en"
      sourceCount={2}
      shownCitationCount={2}
      chunkCitationCount={2}
      edgeCitationCount={0}
      graphContext={null}
      events={[
        {
          eventType: "context.assembled",
          payload: { contextSnapshotId: "snapshot-1", tokenCount: 64 },
        },
        {
          eventType: "stage.completed",
          payload: { stage: "knowledge.answer" },
        },
        {
          eventType: "retrieval.hits_ready",
          payload: { authorizedHitCount: 4 },
        },
        { eventType: "citation.resolved", payload: {} },
        { eventType: "citation.resolved", payload: {} },
      ]}
    />,
  );
  const summary = screen.getByText(
    "Searched 2 sources · 2 passages · 0 relations",
  );
  expect(summary).toBeVisible();
  expect(
    screen.getByText("Retrieval: 4 authorized evidence hits"),
  ).not.toBeVisible();
  await userEvent.click(summary);
  expect(
    screen.getByText("Retrieval: 4 authorized evidence hits"),
  ).toBeVisible();
});

it("distinguishes resolved citation records from references used in visible claims", async () => {
  render(
    <AnswerActivity
      locale="en"
      sourceCount={1}
      shownCitationCount={4}
      chunkCitationCount={4}
      edgeCitationCount={0}
      graphContext={null}
      events={[
        { eventType: "answer.delta", payload: {} },
        ...Array.from({ length: 8 }, () => ({
          eventType: "citation.resolved",
          payload: {},
        })),
      ]}
    />,
  );
  const summary = screen.getByText(
    "Searched 1 source · 4 passages · 0 relations",
  );
  expect(summary).toBeVisible();
  await userEvent.click(summary);
  expect(
    screen.getByText("8 citation records resolved; 4 used by displayed claims"),
  ).toBeVisible();
});

it("shows source count from prop when context event has no sourceCount", () => {
  render(
    <AnswerActivity
      locale="en"
      sourceCount={3}
      shownCitationCount={0}
      chunkCitationCount={0}
      edgeCitationCount={0}
      graphContext={null}
      events={[
        {
          eventType: "context.assembled",
          payload: {
            contextSnapshotId: "snapshot-1",
            tokenCount: 128,
            sourceCount: 99,
          },
        },
      ]}
    />,
  );
  expect(
    screen.getByText("Searched 3 sources · 0 passages · 0 relations"),
  ).toBeVisible();
});

it("renders the Chinese answer summary line with seed entities and relation paths", async () => {
  render(
    <AnswerActivity
      locale="zh"
      sourceCount={2}
      shownCitationCount={3}
      chunkCitationCount={3}
      edgeCitationCount={1}
      graphContext={{
        status: "APPLIED",
        seedCount: 2,
        paths: [["核保流程", "健康告知"]],
        relationCount: 1,
      }}
      events={[]}
    />,
  );
  const summary = screen.getByText("搜索 2 个来源 · 3 段原文 · 1 条关系");
  expect(summary).toBeVisible();
  await userEvent.click(summary);
  expect(screen.getByText("2 个种子实体")).toBeVisible();
  expect(screen.getByText("关系路径: 核保流程 → 健康告知")).toBeVisible();
});

it("falls back to edge citation count without a graph event", () => {
  render(
    <AnswerActivity
      locale="en"
      sourceCount={2}
      shownCitationCount={5}
      chunkCitationCount={3}
      edgeCitationCount={2}
      graphContext={null}
      events={[{ eventType: "answer.delta", payload: {} }]}
    />,
  );
  expect(
    screen.getByText("Searched 2 sources · 3 passages · 2 relations"),
  ).toBeVisible();
});

it("uses singular English units for counts of one", async () => {
  render(
    <AnswerActivity
      locale="en"
      sourceCount={1}
      shownCitationCount={1}
      chunkCitationCount={1}
      edgeCitationCount={0}
      graphContext={{
        status: "APPLIED",
        seedCount: 1,
        paths: [["A", "B"]],
        relationCount: 1,
      }}
      events={[]}
    />,
  );
  const summary = screen.getByText(
    "Searched 1 source · 1 passage · 1 relation",
  );
  expect(summary).toBeVisible();
  await userEvent.click(summary);
  expect(screen.getByText("1 seed entity")).toBeVisible();
});

it("hides the seed/path rows when there is no seed count and no paths", () => {
  render(
    <AnswerActivity
      locale="en"
      sourceCount={2}
      shownCitationCount={2}
      chunkCitationCount={2}
      edgeCitationCount={0}
      graphContext={{
        status: "APPLIED",
        seedCount: 0,
        paths: [],
        relationCount: 0,
      }}
      events={[{ eventType: "answer.delta", payload: {} }]}
    />,
  );
  expect(screen.queryByText(/seed entit/u)).not.toBeInTheDocument();
});

it("describes a running answer using only its selected source snapshot", () => {
  render(<AnswerProgress locale="en" sourceCount={2} />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Using 2 selected sources · Generating answer…",
  );
});
