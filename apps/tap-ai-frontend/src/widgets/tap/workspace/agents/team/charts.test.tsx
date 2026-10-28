import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Sparkline, TrendChart } from "./charts";

describe("Sparkline", () => {
  it("renders an accessible image and tolerates empty or single points", () => {
    const { rerender } = render(<Sparkline points={[]} label="empty" />);
    expect(screen.getByRole("img", { name: "empty" })).toBeInTheDocument();
    rerender(<Sparkline points={[3]} label="one" />);
    expect(screen.getByRole("img", { name: "one" })).toBeInTheDocument();
    rerender(<Sparkline points={[1, 4, 2]} label="three" />);
    expect(
      screen.getByRole("img", { name: "three" }).querySelector("polyline"),
    ).not.toBeNull();
  });
});

describe("TrendChart", () => {
  it("shows the hovered bucket value", () => {
    render(
      <TrendChart
        unit="times"
        series={[
          {
            label: "Runs",
            points: [
              { bucket: "2026-10-01", value: 2 },
              { bucket: "2026-10-02", value: 7 },
            ],
          },
        ]}
      />,
    );
    fireEvent.mouseEnter(screen.getAllByTestId("trend-hit")[1]);
    expect(screen.getByText("2026-10-02 · Runs: 7 times")).toBeInTheDocument();
  });

  it("reads each series by bucket key, not array position", () => {
    render(
      <TrendChart
        unit="times"
        series={[
          {
            label: "Runs",
            points: [
              { bucket: "2026-10-01", value: 2 },
              { bucket: "2026-10-02", value: 7 },
            ],
          },
          { label: "Failures", points: [{ bucket: "2026-10-02", value: 1 }] },
        ]}
      />,
    );
    const hits = screen.getAllByTestId("trend-hit");
    expect(hits).toHaveLength(2);
    fireEvent.mouseEnter(hits[1]);
    expect(
      screen.getByText("2026-10-02 · Failures: 1 times"),
    ).toBeInTheDocument();
    fireEvent.mouseEnter(hits[0]);
    expect(screen.queryByText(/Failures:/)).toBeNull();
  });

  it("moves the readout with the arrow keys", async () => {
    render(
      <TrendChart
        unit="times"
        series={[
          {
            label: "Runs",
            points: [
              { bucket: "2026-10-01", value: 2 },
              { bucket: "2026-10-02", value: 7 },
              { bucket: "2026-10-03", value: 4 },
            ],
          },
        ]}
      />,
    );
    const chart = screen.getByRole("group", { name: /Runs/ });
    expect(chart).toHaveAttribute("tabindex", "0");
    await userEvent.tab();
    expect(chart).toHaveFocus();
    // Focus lands on the latest bucket.
    expect(screen.getByText("2026-10-03 · Runs: 4 times")).toBeInTheDocument();
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(screen.getByText("2026-10-01 · Runs: 2 times")).toBeInTheDocument();
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByText("2026-10-01 · Runs: 2 times")).toBeInTheDocument();
    await userEvent.keyboard("{End}");
    expect(screen.getByText("2026-10-03 · Runs: 4 times")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByText(/Runs: /)).toBeNull();
  });

  it("rounds the y-axis to clean ticks, labels the top with the unit and legends several series", () => {
    render(
      <TrendChart
        unit="times"
        series={[
          {
            label: "Runs",
            points: [
              { bucket: "2026-10-01", value: 2 },
              { bucket: "2026-10-02", value: 59 },
            ],
          },
          { label: "Failures", points: [{ bucket: "2026-10-02", value: 1 }] },
        ]}
        hint="Use arrows"
      />,
    );
    // 59 rounds up to a clean 60 with 20-step gridlines.
    expect(screen.getByText("60 times")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
    const chart = screen.getByRole("group", { name: /Runs/ });
    expect(chart).toHaveAttribute("aria-roledescription", "chart");
    expect(chart).toHaveAttribute("aria-description", "Use arrows");
    const legend = screen.getByRole("list", { hidden: true });
    expect(legend).toHaveTextContent("Runs");
    expect(legend).toHaveTextContent("Failures");
  });

  it("shows no legend for a single series", () => {
    render(
      <TrendChart
        unit="times"
        series={[
          { label: "Runs", points: [{ bucket: "2026-10-01", value: 2 }] },
        ]}
      />,
    );
    expect(screen.queryByRole("list", { hidden: true })).toBeNull();
  });

  it("omits an empty unit without stray spaces or parentheses", () => {
    render(
      <TrendChart
        unit=""
        series={[
          { label: "Runs", points: [{ bucket: "2026-10-01", value: 12 }] },
        ]}
      />,
    );
    // 12 rounds up to a clean 15; the top tick is the bare number.
    expect(screen.getByText("15").textContent).toBe("15");
    expect(screen.getByRole("group")).toHaveAccessibleName("Runs");
  });

  it("does not throw without data", () => {
    render(<TrendChart unit="times" series={[]} />);
    expect(screen.queryAllByTestId("trend-hit")).toHaveLength(0);
  });

  it("draws a tooltip card listing every series at the hovered bucket", () => {
    const { container } = render(
      <TrendChart
        unit="times"
        series={[
          {
            label: "Runs",
            points: [
              { bucket: "2026-10-01", label: "Oct 1", value: 2 },
              { bucket: "2026-10-02", label: "Oct 2", value: 7 },
            ],
          },
          { label: "Failures", points: [{ bucket: "2026-10-02", value: 1 }] },
        ]}
      />,
    );
    fireEvent.mouseEnter(screen.getAllByTestId("trend-hit")[1]);
    const tooltip = container.querySelector(".tap-chart-tooltip")!;
    expect(tooltip).toHaveAttribute("aria-hidden", "true");
    expect(tooltip).toHaveTextContent("Oct 2");
    expect(tooltip).toHaveTextContent("Runs7 times");
    expect(tooltip).toHaveTextContent("Failures1 times");
    expect(container.querySelector(".tap-chart-crosshair")).not.toBeNull();
    expect(container.querySelectorAll(".tap-chart-dot")).toHaveLength(2);
  });
});

describe("Sparkline end dot", () => {
  it("marks the latest point", () => {
    const { container } = render(<Sparkline points={[1, 4]} label="x" />);
    expect(container.querySelector(".tap-team-sparkline-dot")).not.toBeNull();
  });
});
