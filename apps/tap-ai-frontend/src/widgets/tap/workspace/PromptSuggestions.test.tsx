import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type { PromptSuggestionItem } from "../../../features/knowledge/api/types";
import { WORKSPACE_COPY } from "./copy";
import { PromptSuggestions } from "./PromptSuggestions";

const copy = WORKSPACE_COPY.en;

function item(
  id: string,
  question: string,
  sources: PromptSuggestionItem["sources"] = [{ sourceId: "src-a", name: "A" }],
): PromptSuggestionItem {
  return { id, question, sources };
}

it("shows four cards and rotates with wrap-around", async () => {
  const items = [
    item("1", "Question 1"),
    item("2", "Question 2"),
    item("3", "Question 3"),
    item("4", "Question 4"),
    item("5", "Question 5"),
    item("6", "Question 6"),
  ];
  const user = userEvent.setup();
  render(
    <PromptSuggestions
      copy={copy}
      state={{ kind: "ready", items }}
      onPick={vi.fn()}
    />,
  );

  const group = screen.getByRole("group", { name: "Suggested questions" });
  expect(
    within(group)
      .getAllByRole("button")
      .map((button) => button.textContent),
  ).toEqual([
    expect.stringContaining("Question 1"),
    expect.stringContaining("Question 2"),
    expect.stringContaining("Question 3"),
    expect.stringContaining("Question 4"),
  ]);

  await user.click(screen.getByRole("button", { name: "Show others" }));

  expect(
    within(screen.getByRole("group", { name: "Suggested questions" }))
      .getAllByRole("button")
      .map((button) => button.textContent),
  ).toEqual([
    expect.stringContaining("Question 5"),
    expect.stringContaining("Question 6"),
    expect.stringContaining("Question 1"),
    expect.stringContaining("Question 2"),
  ]);
});

it("hides the refresh action with four or fewer suggestions", () => {
  const items = [
    item("1", "Question 1"),
    item("2", "Question 2"),
    item("3", "Question 3"),
    item("4", "Question 4"),
  ];
  render(
    <PromptSuggestions
      copy={copy}
      state={{ kind: "ready", items }}
      onPick={vi.fn()}
    />,
  );

  expect(
    screen.queryByRole("button", { name: "Show others" }),
  ).not.toBeInTheDocument();
});

it("shows four skeletons while loading", () => {
  const { container } = render(
    <PromptSuggestions
      copy={copy}
      state={{ kind: "loading" }}
      onPick={vi.fn()}
    />,
  );

  const group = container.querySelector('[aria-busy="true"]');
  expect(group).not.toBeNull();
  expect(group).toHaveAttribute("aria-label", "Suggested questions");
  expect(group?.querySelectorAll(".ant-skeleton-button")).toHaveLength(4);
});

it("renders nothing when hidden", () => {
  const { container } = render(
    <PromptSuggestions
      copy={copy}
      state={{ kind: "hidden" }}
      onPick={vi.fn()}
    />,
  );

  expect(container).toBeEmptyDOMElement();
});

it("attributes one or many sources", () => {
  const items = [
    item("1", "Question 1", [{ sourceId: "src-a", name: "A" }]),
    item("2", "Question 2", [
      { sourceId: "src-a", name: "A" },
      { sourceId: "src-b", name: "B" },
    ]),
  ];
  render(
    <PromptSuggestions
      copy={copy}
      state={{ kind: "ready", items }}
      onPick={vi.fn()}
    />,
  );

  expect(screen.getByText("Based on A")).toBeVisible();
  expect(screen.getByText("Based on A and 1 more")).toBeVisible();
});
