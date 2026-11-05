import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { expect, it } from "vitest";

import {
  AnswerActivity,
  AnswerProgress,
  AssistantResponse,
  shouldShowAnswerFeedback,
} from "./TapperWorkspace";
import type { AssistantTurn } from "./workspace/model";
import { WORKSPACE_COPY } from "./workspace/copy";

function queryWrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

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
        { eventType: "answer.draft", payload: {} },
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
        querySeedCount: 0,
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
      events={[{ eventType: "answer.draft", payload: {} }]}
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
        querySeedCount: 0,
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
        querySeedCount: 0,
        paths: [],
        relationCount: 0,
      }}
      events={[{ eventType: "answer.draft", payload: {} }]}
    />,
  );
  expect(screen.queryByText(/seed entit/u)).not.toBeInTheDocument();
});

it("renders a tool step row with round, tool name and duration (en)", async () => {
  render(
    <AnswerActivity
      locale="en"
      sourceCount={1}
      shownCitationCount={1}
      chunkCitationCount={1}
      edgeCitationCount={0}
      graphContext={null}
      events={[]}
      steps={[
        {
          stepId: "turn-1:r1:c0",
          tool: "knowledge.search",
          status: "completed",
          summary: "Found 3 hits",
          durationMs: 120,
          toolRound: 1,
        },
      ]}
    />,
  );
  const summary = screen.getByText(
    "Searched 1 source · 1 passage · 0 relations",
  );
  await userEvent.click(summary);
  expect(
    screen.getByText(
      "Round 1 · Knowledge search · Done · Found 3 hits · 120 ms",
    ),
  ).toBeVisible();
});

it("renders a tool step row with round, tool name and duration (zh)", async () => {
  render(
    <AnswerActivity
      locale="zh"
      sourceCount={1}
      shownCitationCount={1}
      chunkCitationCount={1}
      edgeCitationCount={0}
      graphContext={null}
      events={[]}
      steps={[
        {
          stepId: "turn-1:r2:c1",
          tool: "graph.relations",
          status: "rejected",
          summary: "来源超出范围",
          durationMs: 5,
          toolRound: 2,
        },
      ]}
    />,
  );
  const summary = screen.getByText("搜索 1 个来源 · 1 段原文 · 0 条关系");
  await userEvent.click(summary);
  expect(
    screen.getByText("第 2 轮 · 关系分析 · 已拒绝 · 来源超出范围 · 5 ms"),
  ).toBeVisible();
});

it("describes a running answer using only its selected source snapshot", () => {
  render(<AnswerProgress locale="en" sourceCount={2} />);
  expect(screen.getByRole("status")).toHaveTextContent(
    "Using 2 selected sources · Generating answer…",
  );
});

it("clarification answer shows needs-more-information label without citation warning", () => {
  const turn: AssistantTurn = {
    id: "turn-1",
    intent: "answer",
    locale: "zh",
    modelId: "model-1",
    prompt: "报销多少？",
    sourceReferences: [],
    status: "completed",
    response: {
      traceId: "trace-1",
      queryPlanId: "plan-1",
      contextSnapshotId: "context-1",
      corpusVersion: "v1",
      retrievalProfileId: "quick",
      degradedMode: false,
      answer: "请问您指的是哪一份保单？",
      answerKind: "clarification",
      abstained: false,
      claims: [],
      citations: [],
    },
  };
  render(
    createElement(AssistantResponse, {
      contentCopy: WORKSPACE_COPY.zh,
      turn,
      onOpenCitation: () => {},
      onRetryConversation: () => {},
    }),
    { wrapper: queryWrapper },
  );

  expect(screen.getByText("需要补充信息")).toBeVisible();
  expect(screen.getByText("请问您指的是哪一份保单？")).toBeVisible();
  expect(screen.queryByText("回答格式无法核验，请重新提问。")).toBeNull();
});

it("insights-only answer shows body and Insights metrics without citation warning", () => {
  const turn: AssistantTurn = {
    id: "turn-1",
    intent: "answer",
    locale: "zh",
    modelId: "model-1",
    prompt: "本周首次通过率如何？",
    sourceReferences: [],
    status: "completed",
    response: {
      traceId: "trace-1",
      queryPlanId: "plan-1",
      contextSnapshotId: "context-1",
      corpusVersion: "v1",
      retrievalProfileId: "quick",
      degradedMode: false,
      answer: "本周首次通过率为 0.92。",
      answerKind: "answer",
      abstained: false,
      claims: [],
      citations: [],
      insightsFacts: [
        {
          label: "I1",
          queryId: "insq-1",
          metricId: "first_pass_rate",
          value: 0.92,
          asOf: "2026-01-31T00:00:00Z",
        },
      ],
    },
  };
  render(
    createElement(AssistantResponse, {
      contentCopy: WORKSPACE_COPY.zh,
      turn,
      onOpenCitation: () => {},
      onRetryConversation: () => {},
    }),
    { wrapper: queryWrapper },
  );

  expect(screen.getByText("本周首次通过率为 0.92。")).toBeVisible();
  expect(screen.getByText("Insights 指标")).toBeVisible();
  expect(screen.getByText(/first_pass_rate/)).toBeVisible();
  expect(screen.queryByText("回答格式无法核验，请重新提问。")).toBeNull();
});

it("a claim-less answer without Insights facts still shows the citation format warning", () => {
  const turn: AssistantTurn = {
    id: "turn-1",
    intent: "answer",
    locale: "zh",
    modelId: "model-1",
    prompt: "本周首次通过率如何？",
    sourceReferences: [],
    status: "completed",
    response: {
      traceId: "trace-1",
      queryPlanId: "plan-1",
      contextSnapshotId: "context-1",
      corpusVersion: "v1",
      retrievalProfileId: "quick",
      degradedMode: false,
      answer: "没有依据的回答。",
      answerKind: "answer",
      abstained: false,
      claims: [],
      citations: [],
      insightsFacts: [],
    },
  };
  render(
    createElement(AssistantResponse, {
      contentCopy: WORKSPACE_COPY.zh,
      turn,
      onOpenCitation: () => {},
      onRetryConversation: () => {},
    }),
    { wrapper: queryWrapper },
  );

  expect(screen.getByText("回答格式无法核验，请重新提问。")).toBeVisible();
});

it("gates the answer feedback control to a completed turn with a durable conversation", () => {
  expect(
    shouldShowAnswerFeedback({ status: "completed" }, "conversation-1"),
  ).toBe(true);
  expect(
    shouldShowAnswerFeedback({ status: "running" }, "conversation-1"),
  ).toBe(false);
  expect(shouldShowAnswerFeedback({ status: "queued" }, "conversation-1")).toBe(
    false,
  );
  expect(shouldShowAnswerFeedback({ status: "completed" }, undefined)).toBe(
    false,
  );
});
