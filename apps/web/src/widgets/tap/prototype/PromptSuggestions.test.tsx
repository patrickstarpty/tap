import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PromptSuggestions } from "./PromptSuggestions";
import { PROTOTYPE_COPY } from "./copy";
import type { LibrarySource } from "./model";
import { availablePromptSuggestions } from "./samplePromptSuggestions";
import { setPrototypeFaults } from "./prototypeFaults";

const copy = PROTOTYPE_COPY.en;

afterEach(() => {
  setPrototypeFaults([]);
});

const suggestions = [
  {
    id: "s1",
    question: "Q1 What evidence is required for applicants over 60?",
    sources: [
      { id: "underwriting-v12", name: "Life underwriting guide · v1.2.md" },
    ],
  },
  {
    id: "s2",
    question: "Q2 Do the underwriting test rules cover every boundary?",
    sources: [
      { id: "sample-underwriting", name: "Underwriting test rules.pdf" },
      { id: "underwriting-v12", name: "Life underwriting guide · v1.2.md" },
    ],
  },
  {
    id: "s3",
    question: "Q3 Which fields are mandatory on the disclosure policy?",
    sources: [
      {
        id: "health-disclosure-approved",
        name: "Health disclosure policy · approved.md",
      },
    ],
  },
  {
    id: "s4",
    question: "Q4 How do I look up the premium rate for a non-smoker?",
    sources: [{ id: "premium-rates-xlsx", name: "Premium rates.xlsx" }],
  },
  {
    id: "s5",
    question: "Q5 Which steps of the workflow are covered by test cases?",
    sources: [
      { id: "sample-beneficiary", name: "Beneficiary change workflow.docx" },
    ],
  },
  {
    id: "s6",
    question: "Q6 Do the premium rates align with the risk classes?",
    sources: [
      { id: "sample-test-cases", name: "Beneficiary test cases.xlsx" },
    ],
  },
];

it("shows four cards with source attribution", () => {
  render(
    <PromptSuggestions copy={copy} suggestions={suggestions} onPick={() => {}} />,
  );

  const group = screen.getByRole("group", { name: "Suggested questions" });
  const buttons = within(group).getAllByRole("button");
  expect(buttons).toHaveLength(4);
  expect(buttons[0].textContent?.startsWith("Q1")).toBe(true);

  expect(
    screen.getByText("Based on Life underwriting guide · v1.2.md"),
  ).toBeVisible();
  expect(
    screen.getByText("Based on Underwriting test rules.pdf and 1 more"),
  ).toBeVisible();
});

it("rotates to the next batch with wrap-around", () => {
  render(
    <PromptSuggestions copy={copy} suggestions={suggestions} onPick={() => {}} />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Show others" }));

  const group = screen.getByRole("group", { name: "Suggested questions" });
  expect(within(group).getByText(/^Q5/)).toBeVisible();
  expect(within(group).getByText(/^Q6/)).toBeVisible();
  expect(within(group).getByText(/^Q1/)).toBeVisible();
  expect(within(group).getByText(/^Q2/)).toBeVisible();
});

it("hides the refresh action with four or fewer suggestions", () => {
  render(
    <PromptSuggestions
      copy={copy}
      suggestions={suggestions.slice(0, 4)}
      onPick={() => {}}
    />,
  );

  expect(
    screen.queryByRole("button", { name: "Show others" }),
  ).not.toBeInTheDocument();
});

it("resets to the first batch when suggestions change", () => {
  const { rerender } = render(
    <PromptSuggestions copy={copy} suggestions={suggestions} onPick={() => {}} />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Show others" }));

  const nextSuggestions = [
    { id: "t1", question: "T1 first", sources: [{ id: "a", name: "A" }] },
    { id: "t2", question: "T2 second", sources: [{ id: "b", name: "B" }] },
    { id: "t3", question: "T3 third", sources: [{ id: "c", name: "C" }] },
    { id: "t4", question: "T4 fourth", sources: [{ id: "d", name: "D" }] },
    { id: "t5", question: "T5 fifth", sources: [{ id: "e", name: "E" }] },
  ];
  rerender(
    <PromptSuggestions
      copy={copy}
      suggestions={nextSuggestions}
      onPick={() => {}}
    />,
  );

  const group = screen.getByRole("group", { name: "Suggested questions" });
  expect(within(group).getByText(/^T1/)).toBeVisible();
  expect(within(group).getByText(/^T2/)).toBeVisible();
  expect(within(group).getByText(/^T3/)).toBeVisible();
  expect(within(group).getByText(/^T4/)).toBeVisible();
  expect(within(group).queryByText(/^T5/)).not.toBeInTheDocument();
});

it("calls onPick with the chosen suggestion", () => {
  const onPick = vi.fn();
  render(
    <PromptSuggestions copy={copy} suggestions={suggestions} onPick={onPick} />,
  );

  fireEvent.click(screen.getByRole("button", { name: /^Q1/ }));

  expect(onPick).toHaveBeenCalledWith(suggestions[0]);
});

it("renders nothing when there are no suggestions", () => {
  const { container } = render(
    <PromptSuggestions copy={copy} suggestions={[]} onPick={() => {}} />,
  );

  expect(container).toBeEmptyDOMElement();
});

it("shows four skeletons while suggestions load", () => {
  setPrototypeFaults(["suggestions-loading"]);

  const { container } = render(
    <PromptSuggestions copy={copy} suggestions={suggestions} onPick={() => {}} />,
  );

  const region = container.querySelector('[aria-busy="true"]');
  expect(region).not.toBeNull();
  expect(region).toHaveAttribute("aria-label", "Suggested questions");
  expect(container.querySelectorAll(".ant-skeleton")).toHaveLength(4);
  expect(screen.queryAllByRole("button")).toHaveLength(0);
});

it("renders nothing when suggestions fail to load", () => {
  setPrototypeFaults(["suggestions-load-failed"]);

  const { container } = render(
    <PromptSuggestions copy={copy} suggestions={suggestions} onPick={() => {}} />,
  );

  expect(screen.queryByRole("group")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(container).toBeEmptyDOMElement();
});

describe("availablePromptSuggestions", () => {
  function librarySource(id: string, name: string): LibrarySource {
    return {
      id,
      name,
      origin: "knowledge-base",
      type: "PDF",
      status: "ready",
      description: "",
    };
  }

  const allReadySources: LibrarySource[] = [
    librarySource("underwriting-v12", "Life underwriting guide · v1.2.md"),
    librarySource(
      "health-disclosure-approved",
      "Health disclosure policy · approved.md",
    ),
    librarySource("premium-rates-xlsx", "Premium rates.xlsx"),
    librarySource("sample-underwriting", "Underwriting test rules.pdf"),
    librarySource("sample-beneficiary", "Beneficiary change workflow.docx"),
    librarySource("sample-test-cases", "Beneficiary test cases.xlsx"),
  ];

  it("hides suggestions whose sources are not all ready", () => {
    const withoutPremiumRates = allReadySources.filter(
      (source) => source.id !== "premium-rates-xlsx",
    );

    const result = availablePromptSuggestions("en", withoutPremiumRates);

    expect(
      result.some((suggestion) =>
        suggestion.sources.some((source) => source.id === "premium-rates-xlsx"),
      ),
    ).toBe(false);
    expect(availablePromptSuggestions("en", [])).toEqual([]);
  });

  it("includes a two-source suggestion combining sample-underwriting with another ready source", () => {
    const result = availablePromptSuggestions("en", allReadySources);

    expect(result).toHaveLength(6);
    expect(
      result.some(
        (suggestion) =>
          suggestion.sources.length >= 2 &&
          suggestion.sources.some((source) => source.id === "sample-underwriting"),
      ),
    ).toBe(true);
  });

  it("follows the interface language", () => {
    const result = availablePromptSuggestions("zh", allReadySources);

    expect(result.length).toBeGreaterThan(0);
    expect(result.every((s) => /[一-鿿]/.test(s.question))).toBe(true);
  });
});
