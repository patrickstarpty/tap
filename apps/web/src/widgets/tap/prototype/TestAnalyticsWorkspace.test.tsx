import { render, screen, fireEvent, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { TestAnalyticsWorkspace } from "./TestAnalyticsWorkspace";

it("renders a localized, scoped quality overview", () => {
  render(<TestAnalyticsWorkspace />);
  expect(screen.getByRole("heading", { name: "测试分析" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "质量趋势" })).toBeInTheDocument();
  expect(screen.getByLabelText("测试计划")).toBeInTheDocument();
  expect(
    screen.getByText(/合成演示快照 · 2026-08-24 至 2026-09-06/),
  ).toBeInTheDocument();
});

it("shows and clears an explicit date drill-down", () => {
  render(<TestAnalyticsWorkspace />);
  fireEvent.click(
    screen.getByRole("button", { name: "分析质量趋势 2026-09-06" }),
  );
  expect(screen.getByText("日期筛选 · 2026-09-06")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "清除日期筛选" }));
  expect(screen.queryByText("日期筛选 · 2026-09-06")).not.toBeInTheDocument();
  expect(
    screen.getByText(/合成演示快照 · 2026-08-24 至 2026-09-06/),
  ).toBeInTheDocument();
});

it("supports failure and flaky analysis tabs with search", () => {
  render(<TestAnalyticsWorkspace />);
  const overview = screen.getByRole("tab", { name: "概览" });
  const failure = screen.getByRole("tab", { name: /失败分析/ });
  expect(overview).toHaveAttribute("aria-selected", "true");
  fireEvent.click(failure);
  expect(failure).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("heading", { name: "失败分析" })).toBeInTheDocument();
  const failurePanel = screen.getByRole("tabpanel", { name: /失败分析/ });
  expect(within(failurePanel).queryByText("passed")).not.toBeInTheDocument();
  expect(within(failurePanel).getAllByText(/BUILD-/).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("tab", { name: /不稳定测试/ }));
  expect(
    screen.getByRole("heading", { name: "不稳定测试" }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("搜索测试或执行 ID"), {
    target: { value: "SC-01" },
  });
  expect(screen.getAllByText(/SC-01/).length).toBeGreaterThan(0);
});

it("opens execution evidence and shows retries", () => {
  render(<TestAnalyticsWorkspace />);
  fireEvent.click(screen.getByRole("tab", { name: /不稳定测试/ }));
  fireEvent.click(screen.getAllByRole("button", { name: /SC-01/ })[0]!);
  const dialog = screen.getByRole("dialog", { name: "执行证据" });
  expect(within(dialog).getByText(/尝试 1/)).toBeInTheDocument();
  expect(within(dialog).getByText(/尝试 2/)).toBeInTheDocument();
});

it("localizes English controls and represents an empty scope honestly", () => {
  render(<TestAnalyticsWorkspace locale="en" />);
  expect(screen.getByLabelText("Test plan")).toBeInTheDocument();
  expect(screen.getByLabelText("Environment")).toBeInTheDocument();
  expect(screen.getByLabelText("Period")).toBeInTheDocument();
  expect(
    screen.getByText(/Synthetic demo snapshot · 2026-08-24 to 2026-09-06/),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Test plan"), {
    target: { value: "TP-102" },
  });
  expect(screen.getByText("—")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(
    "No execution evidence in this scope",
  );
});

it("exports scoped execution data with synthetic provenance", async () => {
  const createObjectURL = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:test-analytics");
  const revokeObjectURL = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => undefined);
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(() => undefined);
  render(<TestAnalyticsWorkspace locale="en" />);

  fireEvent.change(screen.getByLabelText("Environment"), {
    target: { value: "staging" },
  });

  fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));

  const blob = createObjectURL.mock.calls[0]![0] as Blob;
  const lines = (await blob.text()).trim().split("\n");
  const widths = lines.map(
    (line) => line.match(/(?:^|,)(?:"(?:[^"]|"")*"|[^,]*)/g)?.length,
  );
  expect(new Set(widths)).toEqual(new Set([14]));
  expect(lines[0]).toContain("Data source,Snapshot end");
  expect(lines.slice(1).every((line) => line.includes('"staging"'))).toBe(true);
  expect(lines.slice(1).every((line) => line.includes('"2026-09-06"'))).toBe(
    true,
  );
  expect(click).toHaveBeenCalledOnce();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:test-analytics");
});
