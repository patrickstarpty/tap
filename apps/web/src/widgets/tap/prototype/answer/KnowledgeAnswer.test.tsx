import { fireEvent, render, screen } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { KnowledgeAnswer } from "./KnowledgeAnswer";
import type { AssistantTurn } from "../model";

function turn(overrides: Partial<AssistantTurn> = {}): AssistantTurn {
  return {
    id: "turn-1",
    intent: "answer",
    locale: "en",
    modelId: "gpt-5.6-sol",
    prompt: "What blocks submission?",
    sourceReferences: [
      {
        id: "sample-beneficiary",
        name: "Beneficiary change workflow.docx",
        origin: "page-local",
      },
      {
        id: "sample-test-cases",
        name: "Beneficiary test cases.xlsx",
        origin: "page-local",
      },
    ],
    ...overrides,
  };
}

it("shows the queued stage before generating", () => {
  render(
    <KnowledgeAnswer
      turn={turn({ answerState: "queued" })}
      onRetry={vi.fn()}
      onStop={vi.fn()}
      onOpenCitation={vi.fn()}
    />,
  );
  expect(screen.getByText("Waiting to start…")).toBeVisible();
  expect(screen.getByRole("button", { name: "Stop" })).toBeVisible();
});

it("lists both conflicting citations", () => {
  const onOpenCitation = vi.fn();
  render(
    <KnowledgeAnswer
      turn={turn({ answerState: "conflict" })}
      onRetry={vi.fn()}
      onStop={vi.fn()}
      onOpenCitation={onOpenCitation}
    />,
  );
  expect(
    screen.getByText("The two sources reach different conclusions."),
  ).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", { name: "[1] Beneficiary change workflow.docx" }),
  );
  expect(onOpenCitation).toHaveBeenCalledWith(
    expect.objectContaining({ turnId: "turn-1", index: 1 }),
  );
  expect(
    screen.getByRole("button", { name: "[2] Beneficiary test cases.xlsx" }),
  ).toBeVisible();
});

it("offers resubmit when sources changed", () => {
  const onRetry = vi.fn();
  render(
    <KnowledgeAnswer
      turn={turn({ answerState: "source-changed" })}
      onRetry={onRetry}
      onStop={vi.fn()}
      onOpenCitation={vi.fn()}
    />,
  );
  expect(
    screen.getByText(
      "Sources were updated while answering. Please resubmit.",
    ),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Resubmit" }));
  expect(onRetry).toHaveBeenCalled();
});

it("offers retry after updates stop", () => {
  const onRetry = vi.fn();
  render(
    <KnowledgeAnswer
      turn={turn({ answerState: "interrupted" })}
      onRetry={onRetry}
      onStop={vi.fn()}
      onOpenCitation={vi.fn()}
    />,
  );
  expect(
    screen.getByText("Conversation updates stopped. Your message is saved."),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalled();
});

it("warns about limited retrieval above the answer", () => {
  render(
    <KnowledgeAnswer
      turn={turn({ answerState: "completed", retrievalLimited: true })}
      onRetry={vi.fn()}
      onStop={vi.fn()}
      onOpenCitation={vi.fn()}
    />,
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "Some sources could not be searched. This answer uses the remaining sources.",
  );
});
