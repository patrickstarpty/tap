import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { AnswerFeedback, type FeedbackValue } from "./AnswerFeedback";

function Wrapper({ locale }: { locale: "en" | "zh" }) {
  const [value, setValue] = useState<FeedbackValue | null>(null);
  return <AnswerFeedback locale={locale} value={value} onChange={setValue} />;
}

function ControlledWrapper({
  locale,
  onChangeSpy,
}: {
  locale: "en" | "zh";
  onChangeSpy: (next: FeedbackValue | null) => void;
}) {
  const [value, setValue] = useState<FeedbackValue | null>(null);
  return (
    <AnswerFeedback
      locale={locale}
      value={value}
      onChange={(next) => {
        onChangeSpy(next);
        setValue(next);
      }}
    />
  );
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

it("hides the comment box after submit", () => {
  render(<Wrapper locale="en" />);
  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  expect(
    screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
  ).not.toBeInTheDocument();
});

it("hides the comment box after skip", () => {
  render(<Wrapper locale="en" />);
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

it("gives each open comment box a distinct id and accessible name", () => {
  render(
    <>
      <Wrapper locale="en" />
      <Wrapper locale="en" />
    </>,
  );
  const notHelpfulButtons = screen.getAllByRole("button", {
    name: "Not helpful",
  });
  fireEvent.click(notHelpfulButtons[0]);
  fireEvent.click(notHelpfulButtons[1]);
  const textboxes = screen.getAllByRole("textbox", {
    name: "Add a comment (optional)",
  });
  expect(textboxes).toHaveLength(2);
  const ids = textboxes.map((el) => el.id);
  expect(ids[0]).toBeTruthy();
  expect(ids[1]).toBeTruthy();
  expect(ids[0]).not.toBe(ids[1]);
});

it("focuses the comment textarea as soon as it opens", () => {
  render(<Wrapper locale="en" />);
  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }));
  expect(
    screen.getByRole("textbox", { name: "Add a comment (optional)" }),
  ).toHaveFocus();
});

it("returns focus to the not-helpful button after submit", () => {
  render(<Wrapper locale="en" />);
  const down = screen.getByRole("button", { name: "Not helpful" });
  fireEvent.click(down);
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  expect(down).toHaveFocus();
});

it("returns focus to the not-helpful button after skip", () => {
  render(<Wrapper locale="en" />);
  const down = screen.getByRole("button", { name: "Not helpful" });
  fireEvent.click(down);
  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  expect(down).toHaveFocus();
});

it("retracts not helpful feedback on a second click", () => {
  render(<Wrapper locale="en" />);
  const down = screen.getByRole("button", { name: "Not helpful" });
  fireEvent.click(down);
  fireEvent.click(down);
  expect(down).toHaveAttribute("aria-pressed", "false");
  expect(
    screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
  ).not.toBeInTheDocument();
});

it("only calls onChange on actual rating changes, including retracting a down rating", () => {
  const onChangeSpy = vi.fn();
  render(<ControlledWrapper locale="en" onChangeSpy={onChangeSpy} />);
  const up = screen.getByRole("button", { name: "Helpful" });
  const down = screen.getByRole("button", { name: "Not helpful" });

  fireEvent.click(up);
  expect(onChangeSpy).toHaveBeenLastCalledWith({
    rating: "up",
    comment: null,
  });

  fireEvent.click(up);
  expect(onChangeSpy).toHaveBeenLastCalledWith(null);

  fireEvent.click(down);
  expect(onChangeSpy).toHaveBeenLastCalledWith({
    rating: "down",
    comment: null,
  });

  const textbox = screen.getByRole("textbox", {
    name: "Add a comment (optional)",
  });
  fireEvent.change(textbox, { target: { value: "  needs more detail  " } });
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  expect(onChangeSpy).toHaveBeenLastCalledWith({
    rating: "down",
    comment: "needs more detail",
  });

  // Re-clicking an already-selected 👎 retracts it, symmetric with 👍, and
  // closes the comment box.
  fireEvent.click(down);
  expect(onChangeSpy).toHaveBeenLastCalledWith(null);
  expect(down).toHaveAttribute("aria-pressed", "false");
  expect(
    screen.queryByRole("textbox", { name: "Add a comment (optional)" }),
  ).not.toBeInTheDocument();

  // Switching from down to up still fires a change (not just none<->up).
  fireEvent.click(down);
  expect(onChangeSpy).toHaveBeenLastCalledWith({
    rating: "down",
    comment: null,
  });
  fireEvent.click(up);
  expect(onChangeSpy).toHaveBeenLastCalledWith({
    rating: "up",
    comment: null,
  });
});

it("does not call onChange on skip", () => {
  const onChangeSpy = vi.fn();
  render(<ControlledWrapper locale="en" onChangeSpy={onChangeSpy} />);
  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }));
  onChangeSpy.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Skip" }));
  expect(onChangeSpy).not.toHaveBeenCalled();
});

it("submits a null comment for whitespace-only input", () => {
  const onChangeSpy = vi.fn();
  render(<ControlledWrapper locale="en" onChangeSpy={onChangeSpy} />);
  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }));
  const textbox = screen.getByRole("textbox", {
    name: "Add a comment (optional)",
  });
  fireEvent.change(textbox, { target: { value: "   " } });
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  expect(onChangeSpy).toHaveBeenLastCalledWith({
    rating: "down",
    comment: null,
  });
});

it("styles submit as primary and skip as text", () => {
  render(<Wrapper locale="en" />);
  fireEvent.click(screen.getByRole("button", { name: "Not helpful" }));
  expect(
    screen.getByRole("button", { name: "Submit" }).className,
  ).toContain("ant-btn-primary");
  expect(screen.getByRole("button", { name: "Skip" }).className).toContain(
    "ant-btn-text",
  );
});
