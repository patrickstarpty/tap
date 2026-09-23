import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import {
  displayExecutionLog,
  FailureKnowledgeExplanation,
} from "./ReportIntakePrototype";
import { TestAnalyticsWorkspace } from "./TestAnalyticsWorkspace";

it("renders the BrowserStack dashboard composition", () => {
  render(<TestAnalyticsWorkspace locale="en" />);
  expect(screen.getByRole("heading", { name: "Demo Dashboard" })).toBeVisible();
  expect(screen.getByText("Dashboards")).toBeVisible();
  expect(screen.getByRole("button", { name: "Add Widgets" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Share" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Filters" })).toBeVisible();
  expect(screen.getByRole("button", { name: "30D" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const dashboard = screen.getByRole("region", { name: "Dashboard widgets" });
  for (const widget of [
    "Summary",
    "Build Summary",
    "Stability",
    "Flakiness",
    "Browser wise summary",
    "Build Performance",
  ]) {
    expect(
      within(dashboard).getByRole("heading", { name: widget }),
    ).toBeVisible();
  }
});

it("applies dashboard filters to every widget", () => {
  render(<TestAnalyticsWorkspace locale="en" />);
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  const filters = screen.getByRole("dialog", { name: "Dashboard filters" });
  fireEvent.change(within(filters).getByLabelText("Environment"), {
    target: { value: "staging" },
  });
  fireEvent.click(within(filters).getByRole("button", { name: "Apply" }));
  expect(screen.getByText("Environment: Staging")).toBeVisible();
  expect(screen.getByTestId("tests-summary-value")).toHaveTextContent("42");
});

it("drills down from a widget into contributing tests", () => {
  render(<TestAnalyticsWorkspace locale="en" />);
  fireEvent.click(
    screen.getByRole("button", { name: "View Flakiness breakdown" }),
  );
  const drilldown = screen.getByRole("dialog", { name: "Flakiness breakdown" });
  expect(
    within(drilldown).getByRole("heading", { name: "Flakiness" }),
  ).toBeVisible();
  expect(
    within(drilldown).getAllByRole("button", { name: /Inspect execution/ })
      .length,
  ).toBeGreaterThan(0);
  fireEvent.click(
    within(drilldown).getAllByRole("button", { name: /Inspect execution/ })[0]!,
  );
  expect(
    screen.getByRole("dialog", { name: "Execution evidence" }),
  ).toBeVisible();
});

it("opens the BrowserStack widget catalog", () => {
  render(<TestAnalyticsWorkspace locale="en" />);
  fireEvent.click(screen.getByRole("button", { name: "Add Widgets" }));
  const catalog = screen.getByRole("dialog", { name: "Add Widgets" });
  expect(within(catalog).getByText("Choose a widget")).toBeVisible();
  expect(within(catalog).getByText("Build Summary")).toBeVisible();
});

it("exports the currently filtered synthetic data", async () => {
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
  fireEvent.click(screen.getByRole("button", { name: "Download dashboard" }));
  const blob = createObjectURL.mock.calls[0]![0] as Blob;
  expect((await blob.text()).split("\n")[0]).toContain(
    "Data source,Snapshot end",
  );
  expect(click).toHaveBeenCalledOnce();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:test-analytics");
});

it("selects builds and reflects report availability without a scenario switch", () => {
  render(<TestAnalyticsWorkspace locale="zh" reviewPrototype />);
  fireEvent.change(screen.getByLabelText("构建"), {
    target: { value: "BUILD-2868" },
  });
  expect(
    screen.queryByRole("region", { name: "仪表盘组件" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText(/报告缺失，暂不汇总/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("构建"), {
    target: { value: "BUILD-2869" },
  });
  expect(screen.getByText(/报告无法解析，暂不汇总/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("构建"), {
    target: { value: "BUILD-2867" },
  });
  expect(screen.getByTestId("tests-summary-value")).toHaveTextContent("3");
  expect(screen.getByText(/当前仅 1 天/)).toBeVisible();
});

it("requires a supported file before importing and detects a repeated upload", () => {
  render(<TestAnalyticsWorkspace locale="zh" reviewPrototype />);
  fireEvent.change(screen.getByLabelText("构建"), {
    target: { value: "BUILD-2867" },
  });
  fireEvent.click(screen.getByRole("button", { name: "数据接入" }));
  fireEvent.change(screen.getByLabelText("报告格式"), {
    target: { value: "junit" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
  expect(screen.getByRole("alert")).toHaveTextContent("请选择报告文件");
  fireEvent.change(screen.getByLabelText("报告文件"), {
    target: { files: [new File(["bad"], "report.txt")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
  expect(screen.getByRole("alert")).toHaveTextContent("XML");
  const file = new File(["<testsuites/>"], "report.xml");
  fireEvent.change(screen.getByLabelText("报告文件"), {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByText(/pytest|Allure|JUnit/)).not.toBeInTheDocument();
  expect(screen.getByText(/首次通过率不可用/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "数据接入" }));
  fireEvent.change(screen.getByLabelText("报告文件"), {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
  expect(screen.getByRole("alert")).toHaveTextContent("已接收");
});

it.each([
  ["ERR-422", "与知识的关联"],
  ["ERR-503", "知识依据不足"],
])("explains %s from execution evidence", (errorId, heading) => {
  render(
    <FailureKnowledgeExplanation
      format="allure"
      execution={{
        id: "execution-1",
        testId: "test-1",
        buildId: "BUILD-2867",
        attempts: [
          {
            status: "failed",
            errorId,
            durationMs: 20,
            log: "recorded failure",
          },
        ],
      }}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "AI 结合知识解释" }));
  expect(screen.getByText("recorded failure")).toBeVisible();
  expect(screen.getByRole("heading", { name: heading })).toBeVisible();
});

it("labels report metrics and intake consistently in English", () => {
  render(<TestAnalyticsWorkspace locale="en" reviewPrototype />);
  const widgets = screen.getByRole("region", { name: "Dashboard widgets" });
  expect(widgets.textContent).not.toMatch(/[\p{Script=Han}]/u);
  expect(
    within(widgets).getByRole("heading", { name: "First-pass rate" }),
  ).toBeVisible();
  expect(
    within(widgets).getByRole("heading", { name: "Retry recovery share" }),
  ).toBeVisible();
  expect(within(widgets).getByText("Final pass rate")).toBeVisible();
  expect(
    within(widgets).getByRole("heading", { name: "Execution duration" }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Data connections" }));
  expect(screen.getByLabelText("Report file")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Confirm import" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Choose a report file");
});

it("shows failure evidence without fixture metadata in English", () => {
  render(<TestAnalyticsWorkspace locale="en" reviewPrototype />);
  fireEvent.click(screen.getAllByRole("button", { name: /View failure/ })[0]!);
  const evidence = screen.getByRole("dialog", { name: "Execution evidence" });
  expect(evidence).toHaveTextContent(/HTTP|AssertionError/);
  expect(evidence).not.toHaveTextContent(
    /\[fixture\]|\[context\]|\[test\]|Synthetic execution evidence/,
  );
  fireEvent.click(
    within(evidence).getByRole("button", { name: "Explain with knowledge" }),
  );
  expect(
    within(evidence).getByRole("heading", { name: "Report facts" }),
  ).toBeVisible();
  expect(evidence).not.toHaveTextContent(/\[fixture\]|\[context\]|\[test\]/);
});

it("retains error and assertion lines while removing internal log metadata", () => {
  expect(
    displayExecutionLog(
      "[fixture] Synthetic execution evidence\n[context] build context\n[test] test title\nAssertionError: expected 422\n[assertion] response mismatch\n  at test_validation.py:12",
    ),
  ).toBe(
    "AssertionError: expected 422\n[assertion] response mismatch\n  at test_validation.py:12",
  );
  expect(displayExecutionLog(undefined)).toBe("");
});

it.each(["BUILD-2868", "BUILD-2869"])(
  "keeps repaired report state and selection for %s",
  (buildId) => {
    render(<TestAnalyticsWorkspace locale="en" reviewPrototype />);
    fireEvent.change(screen.getByLabelText("Build"), {
      target: { value: buildId },
    });
    expect(
      screen.queryByRole("region", { name: "Dashboard widgets" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Data connections" }));
    fireEvent.change(screen.getByLabelText("Report file"), {
      target: { files: [new File(["results"], "results.zip")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm import" }));
    expect(screen.getByLabelText("Build")).toHaveValue(buildId);
    expect(
      screen.getByRole("region", { name: "Dashboard widgets" }),
    ).toBeVisible();
    expect(screen.getByTestId("tests-summary-value")).toHaveTextContent("0");
    expect(
      screen.getByText(
        /Daily trends need at least 2 dates with data; 0 available/,
      ),
    ).toBeVisible();
    fireEvent.change(screen.getByLabelText("Build"), {
      target: { value: "BUILD-2867" },
    });
    fireEvent.change(screen.getByLabelText("Build"), {
      target: { value: buildId },
    });
    expect(
      screen.getByRole("region", { name: "Dashboard widgets" }),
    ).toBeVisible();
    const option = within(screen.getByLabelText("Build")).getByRole("option", {
      name: new RegExp(buildId),
    });
    expect(option).not.toHaveTextContent(/missing|failed/i);
  },
);

it("shows project insights and keeps technical report formats inside data connections", () => {
  render(<TestAnalyticsWorkspace locale="en" reviewPrototype />);
  expect(screen.getByRole("heading", { name: "Test Insights" })).toBeVisible();
  expect(screen.getByText("Life insurance")).toBeVisible();
  expect(screen.queryByText(/pytest|Allure|JUnit/)).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: /2 builds need attention/ }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Data connections" }));
  const dialog = screen.getByRole("dialog", { name: "Data connections" });
  expect(within(dialog).getByLabelText("Target build")).toBeVisible();
  expect(within(dialog).getByLabelText("Report format")).toBeVisible();
});

it("imports a report for its target build without replacing project-wide evidence", () => {
  render(<TestAnalyticsWorkspace locale="en" reviewPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "Data connections" }));
  fireEvent.change(screen.getByLabelText("Target build"), {
    target: { value: "BUILD-2868" },
  });
  fireEvent.change(screen.getByLabelText("Report format"), {
    target: { value: "junit" },
  });
  fireEvent.change(screen.getByLabelText("Report file"), {
    target: { files: [new File(["<testsuites/>"], "report.xml")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Confirm import" }));
  expect(screen.getByLabelText("Build")).toHaveValue("");
  expect(screen.getByTestId("tests-summary-value")).toHaveTextContent("84");
  expect(
    screen.getByRole("button", { name: /1 build needs attention/ }),
  ).toBeVisible();
  expect(
    screen.queryByText(/First-pass rate unavailable/),
  ).not.toBeInTheDocument();
});
