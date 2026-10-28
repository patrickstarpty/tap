import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { WORKSPACE_COPY } from "../../copy";
import { ActivityHeatmap, heatLevel, heatThresholds } from "./Heatmap";

const copy = WORKSPACE_COPY.en;

function member(points: [string, number][]) {
  return {
    agentKey: "a1",
    name: "Reviewer",
    runs: points.reduce((sum, [, runs]) => sum + runs, 0),
    trend: points.map(([bucketStart, runs]) => ({
      agentKey: "a1",
      bucketStart,
      runs,
      failures: 0,
      costUsd: "0",
      latencyTotalMs: 0,
    })),
  } as never;
}

describe("heat levels", () => {
  it("spreads active days over four quartile steps; zero is its own", () => {
    const scale = heatThresholds([0, 1, 2, 3, 4]);
    expect([0, 1, 2, 3, 4].map((v) => heatLevel(v, scale))).toEqual([
      0, 1, 2, 3, 4,
    ]);
    // A single busy day is the darkest step.
    expect(heatLevel(7, heatThresholds([0, 7, 0]))).toBe(4);
    const two = heatThresholds([3, 9]);
    expect([heatLevel(3, two), heatLevel(9, two)]).toEqual([1, 4]);
  });
});

describe("ActivityHeatmap", () => {
  // 2026-10-01 is a Thursday: the first week is padded from Monday.
  const period = { from: "2026-10-01", to: "2026-10-14" };

  it("lays days out by week with Monday on top and padding outside", () => {
    const { container } = render(
      <ActivityHeatmap
        members={[
          member([
            ["2026-10-02", 3],
            ["2026-10-09", 9],
          ]),
        ]}
        period={period}
        granularity="day"
        copy={copy}
        locale="en"
        runtimeStatus="ok"
      />,
    );
    const cells = [
      ...container.querySelectorAll(".tap-team-heat-grid .tap-team-heat-cell"),
    ];
    // Three whole weeks: Sep 28 – Oct 18.
    expect(cells).toHaveLength(21);
    expect(cells.slice(0, 3).every((c) => c.hasAttribute("data-outside"))).toBe(
      true,
    );
    expect(cells[3]).not.toHaveAttribute("data-outside");
    expect(cells[4]).toHaveAttribute("data-level", "1");
    expect(cells[11]).toHaveAttribute("data-level", "4");
    // Stats: total, active days, busiest day.
    expect(screen.getByText("Days with runs").nextSibling).toHaveTextContent(
      "2 / 14",
    );
    expect(screen.getByText("Busiest day").nextSibling).toHaveTextContent(
      "92026-10-09",
    );
  });

  it("reads a day on hover and moves by day or week with the keyboard", async () => {
    const { container } = render(
      <ActivityHeatmap
        members={[member([["2026-10-09", 9]])]}
        period={period}
        granularity="day"
        copy={copy}
        locale="en"
        runtimeStatus="ok"
      />,
    );
    const cells = container.querySelectorAll(
      ".tap-team-heat-grid .tap-team-heat-cell",
    );
    fireEvent.mouseEnter(cells[11]);
    expect(container.querySelector(".tap-team-heat-tip")).toHaveTextContent(
      "9 runs",
    );
    const grid = screen.getByRole("group", { name: "Runs per day" });
    fireEvent.mouseLeave(grid);
    await userEvent.tab();
    expect(grid).toHaveFocus();
    // Focus lands on the last day of the period (Wed Oct 14).
    expect(screen.getByText(/^2026-10-14 Wed · 0 runs$/)).toBeInTheDocument();
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByText(/^2026-10-07 Wed · 0 runs$/)).toBeInTheDocument();
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByText(/^2026-10-09 Fri · 9 runs$/)).toBeInTheDocument();
  });

  it("falls back to one cell per bucket for weekly periods", () => {
    const { container } = render(
      <ActivityHeatmap
        members={[member([["2026-06-01", 4]])]}
        period={{ from: "2026-06-01", to: "2026-12-31" }}
        granularity="week"
        copy={copy}
        locale="en"
        runtimeStatus="ok"
      />,
    );
    expect(
      container.querySelectorAll(".tap-team-heat-grid .tap-team-heat-cell"),
    ).toHaveLength(31);
    expect(screen.getByText("Busiest bucket")).toBeInTheDocument();
  });

  it("says run data is unavailable instead of drawing empty days", () => {
    const { container } = render(
      <ActivityHeatmap
        members={undefined}
        period={period}
        granularity="day"
        copy={copy}
        locale="en"
        runtimeStatus="unavailable"
      />,
    );
    expect(container).toHaveTextContent(copy.agentTeam.runtimeUnavailable);
    expect(container.querySelector(".tap-team-heat-grid")).toBeNull();
  });
});
