import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
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
