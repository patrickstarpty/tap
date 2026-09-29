import { fireEvent, render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { AnswerEvidence } from "./AnswerEvidence";
import type { AssistantTurn } from "../model";

function completedTurn(overrides: Partial<AssistantTurn> = {}): AssistantTurn {
  return {
    id: "turn-1",
    intent: "answer",
    locale: "en",
    modelId: "gpt-5.6-sol",
    prompt: "What blocks submission?",
    sourceReferences: [
      { id: "src-1", name: "Life underwriting guide · v1.2.md", origin: "knowledge-base" },
    ],
    answerState: "completed",
    ...overrides,
  };
}

it("summarizes the answer trace collapsed by default", () => {
  render(
    <AnswerEvidence
      turn={completedTurn({
        trace: { searchedSources: 3, matchedPassages: 5, citations: 2 },
      })}
    />,
  );
  const trace = screen.getByRole("button", {
    name: /Searched 3 sources · 5 passages matched · 2 citations/,
  });
  expect(trace).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(trace);
  expect(screen.getByText("Search")).toBeVisible();
});

it("groups sources, agents and skills used by the turn", () => {
  render(
    <AnswerEvidence
      turn={completedTurn({
        trace: { searchedSources: 1, matchedPassages: 3, citations: 1 },
        catalogReferences: [
          { id: "a1", kind: "agent", name: "Underwriting analyst" },
          { id: "s1", kind: "skill", name: "bdd-writer" },
        ],
      })}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Sources and configuration used" }),
  );
  expect(screen.getByRole("group", { name: "Knowledge sources" })).toHaveTextContent(
    "Life underwriting guide · v1.2.md",
  );
  expect(screen.getByRole("group", { name: "Agent" })).toHaveTextContent(
    "Underwriting analyst",
  );
  expect(screen.getByRole("group", { name: "Skills" })).toHaveTextContent(
    "bdd-writer",
  );
});

it("renders nothing without a trace", () => {
  const { container } = render(<AnswerEvidence turn={completedTurn()} />);
  expect(container).toBeEmptyDOMElement();
});
