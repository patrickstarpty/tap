import { fireEvent, render, screen } from "@testing-library/react";
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
        unit="runs"
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
    expect(screen.getByText("2026-10-02 · Runs: 7 runs")).toBeInTheDocument();
  });

  it("does not throw without data", () => {
    render(<TrendChart unit="runs" series={[]} />);
    expect(screen.queryAllByTestId("trend-hit")).toHaveLength(0);
  });
});
