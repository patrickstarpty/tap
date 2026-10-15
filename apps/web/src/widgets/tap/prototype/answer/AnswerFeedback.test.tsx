import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import { AnswerFeedback, type FeedbackValue } from "./AnswerFeedback";

function Wrapper({ locale }: { locale: "en" | "zh" }) {
  const [value, setValue] = useState<FeedbackValue | null>(null);
  return <AnswerFeedback locale={locale} value={value} onChange={setValue} />;
}

it("submits helpful feedback and highlights the button", () => {
  render(<Wrapper locale="en" />);
  const up = screen.getByRole("button", { name: "Helpful" });
  expect(up).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(up);
  expect(up).toHaveAttribute("aria-pressed", "true");
});

it("retracts helpful feedback on a second click", () => {
  render(<Wrapper locale="en" />);
  const up = screen.getByRole("button", { name: "Helpful" });
  fireEvent.click(up);
  fireEvent.click(up);
  expect(up).toHaveAttribute("aria-pressed", "false");
});

it("reveals an optional comment box after not helpful", () => {
  render(<Wrapper locale="en" />);
  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }));
  const textbox = screen.getByRole("textbox", {
    name: "Add a comment (optional)",
  });
  expect(textbox).toHaveAttribute("maxLength", "500");
  expect(screen.getByRole("button", { name: "Submit" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Skip" })).toBeVisible();
});

it("reveals the Chinese comment box after not helpful", () => {
  render(<Wrapper locale="zh" />);
  fireEvent.click(screen.getByRole("button", { name: "没帮助" }));
  const textbox = screen.getByRole("textbox", { name: "补充说明（可选）" });
  expect(textbox).toHaveAttribute("maxLength", "500");
  expect(screen.getByRole("button", { name: /^提\s*交$/ })).toBeVisible();
  expect(screen.getByRole("button", { name: /^跳\s*过$/ })).toBeVisible();
});

it("hides the comment box after submit or skip", () => {
  render(<Wrapper locale="en" />);
  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  expect(
    screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
  ).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }));
  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  expect(
    screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
  ).not.toBeInTheDocument();
});

it("hides the comment box when switching to helpful", () => {
  render(<Wrapper locale="en" />);
  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }));
  expect(
    screen.getByRole("textbox", { name: "Add a comment (optional)" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Helpful" }));
  expect(
    screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Helpful" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    screen.getByRole("button", { name: "Not helpful" }),
  ).toHaveAttribute("aria-pressed", "false");
});
