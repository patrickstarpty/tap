import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { AnswerTrace } from "./AnswerTrace";
import { createSampleTrace } from "./sampleTrace";

it("collapsed row shows duration tokens cost and models", () => {
  render(<AnswerTrace trace={createSampleTrace("en")} />);
  const summary = screen.getByRole("button", { name: /qwen-plus/ });
  expect(summary).toHaveTextContent("3.2s");
  expect(summary).toHaveTextContent("1420/286");
  expect(summary).toHaveTextContent("$0.0042+");
  expect(summary).toHaveTextContent("qwen-plus → dashscope/qwen-plus");
});

it("expanding shows indented waterfall with failed span highlighted", () => {
  render(<AnswerTrace trace={createSampleTrace("en")} />);
  fireEvent.click(screen.getByRole("button", { name: /qwen-plus/ }));
  fireEvent.click(screen.getByRole("tab", { name: "Attempt 1" }));
  const failedRow = screen.getByText("chat qwen-plus").closest("li");
  expect(failedRow).toHaveClass("tap-trace-span-error");
  const embedName = screen.getByText("embeddings text-embedding-v3");
  expect(embedName).toHaveStyle({ paddingLeft: "16px" });
});

it("attempt tabs switch visible spans", () => {
  render(<AnswerTrace trace={createSampleTrace("en")} />);
  fireEvent.click(screen.getByRole("button", { name: /qwen-plus/ }));
  expect(screen.getByText("Citation resolution")).toBeVisible();
  expect(
    screen.queryByText("embeddings text-embedding-v3"),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Attempt 1" }));
  expect(screen.getByText("embeddings text-embedding-v3")).toBeVisible();
  expect(screen.queryByText("Citation resolution")).not.toBeInTheDocument();
});

it("model call span opens drawer with request response reasoning tabs", () => {
  render(<AnswerTrace trace={createSampleTrace("en")} />);
  fireEvent.click(screen.getByRole("button", { name: /qwen-plus/ }));
  const row = screen.getByText("chat qwen-plus").closest("li")!;
  fireEvent.click(within(row).getByRole("button", { name: "View call" }));
  expect(screen.getByRole("tab", { name: "Request" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "Response" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "Reasoning" })).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Reasoning" }));
  expect(
    screen.getByText(/Checked underwriting guide section 4/),
  ).toBeVisible();
});

it("default attempt shows retrieval hits that open the source view", () => {
  render(<AnswerTrace trace={createSampleTrace("en")} />);
  fireEvent.click(screen.getByRole("button", { name: /qwen-plus/ }));
  const hit = screen.getByRole("button", {
    name: "Life underwriting guide · v1.2.md",
  });
  fireEvent.click(hit);
  const dialog = screen.getByRole("dialog", { name: "Source citation" });
  expect(dialog).toBeVisible();
  expect(
    within(dialog).getByRole("heading", {
      name: "Life underwriting guide · v1.2.md",
    }),
  ).toBeVisible();
});
