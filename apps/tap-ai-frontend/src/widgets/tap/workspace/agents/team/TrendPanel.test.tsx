import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { WORKSPACE_COPY } from "../../copy";
import { bucketAxis } from "./buckets";
import type { CellContext } from "./memberCells";
import { TrendPanel } from "./TrendPanel";

const copy = WORKSPACE_COPY.en;
const axis = bucketAxis({ from: "2026-10-01", to: "2026-10-03" }, "day");

function member(key: string, runs: number, perDay: number[]) {
  return {
    agentKey: key,
    name: key,
    runs,
    failures: 1,
    costUsd: "0.30",
    latencyTotalMs: 6000,
    trend: perDay.map((value, i) => ({
      agentKey: key,
      bucketStart: axis[i].key,
      runs: value,
      failures: i === 0 ? 1 : 0,
      costUsd: (value / 10).toFixed(2),
      latencyTotalMs: value * 1000,
    })),
  } as never;
}

const members = [
  member("Alpha", 9, [3, 3, 3]),
  member("Bravo", 6, [2, 2, 2]),
  member("Charlie", 5, [1, 2, 2]),
  member("Delta", 4, [0, 2, 2]),
  member("Echo", 3, [1, 1, 1]),
  member("Foxtrot", 2, [0, 0, 2]),
  member("Golf", 1, [1, 0, 0]),
];

function ctx(runtimeStatus: CellContext["runtimeStatus"] = "ok"): CellContext {
  return { copy, locale: "en", timezone: "UTC", runtimeStatus };
}

describe("TrendPanel", () => {
  it("names the Tapper default member by language in the legend", () => {
    const tapper = {
      ...(member("tapper_default", 9, [3, 3, 3]) as object),
      name: "Tapper 默认",
      kind: "tapper_default",
    } as never;
    const chipLabels = (lang: "en" | "zh") => {
      const { unmount } = render(
        <TrendPanel
          members={[tapper]}
          axis={axis}
          ctx={{ ...ctx(), copy: WORKSPACE_COPY[lang] }}
        />,
      );
      const legend = screen.getByRole("list", {
        name: WORKSPACE_COPY[lang].agentTeam.trend.legendLabel,
      });
      const labels = within(legend)
        .getAllByRole("button")
        .map((chip) => chip.getAttribute("aria-label"));
      unmount();
      return labels;
    };
    expect(chipLabels("en")).toEqual(["Tapper 9"]);
    expect(chipLabels("zh")).toEqual(["Tapper 默认 9"]);
  });

  it("stacks the top five Agents and folds the rest into one series", () => {
    render(<TrendPanel members={members} axis={axis} ctx={ctx()} />);
    const legend = screen.getByRole("list", { name: "Show or hide Agents" });
    const chips = within(legend).getAllByRole("button");
    expect(chips.map((chip) => chip.getAttribute("aria-label"))).toEqual([
      "Alpha 9",
      "Bravo 6",
      "Charlie 5",
      "Delta 4",
      "Echo 3",
      "2 others 3",
    ]);
    expect(screen.getByText("30", { selector: "strong" })).toBeVisible();
  });

  it("lists every series and the total at the hovered bucket", () => {
    const { container } = render(
      <TrendPanel members={members} axis={axis} ctx={ctx()} />,
    );
    fireEvent.mouseEnter(screen.getAllByTestId("mix-hit")[2]);
    const tooltip = container.querySelector(".tap-chart-tooltip")!;
    expect(tooltip).toHaveTextContent("2026-10-03");
    expect(tooltip).toHaveTextContent("Alpha3");
    expect(tooltip).toHaveTextContent("2 others2");
    expect(tooltip).toHaveTextContent("Total12");
  });

  it("hides a series from the bars and total but keeps its colour slot", async () => {
    const { container } = render(
      <TrendPanel members={members} axis={axis} ctx={ctx()} />,
    );
    const bars = () =>
      container
        .querySelectorAll(".tap-chart-column")[0]
        .querySelectorAll("path").length;
    expect(bars()).toBe(5);
    const alpha = screen.getByRole("button", { name: "Alpha 9" });
    await userEvent.click(alpha);
    expect(alpha).toHaveAttribute("aria-pressed", "false");
    expect(bars()).toBe(4);
    fireEvent.mouseEnter(screen.getAllByTestId("mix-hit")[0]);
    const tooltip = container.querySelector(".tap-chart-tooltip")!;
    expect(tooltip).toHaveTextContent("Total5");
    // The hidden series is listed, dimmed.
    expect(tooltip.querySelector('[data-muted="true"]')?.textContent).toContain(
      "Alpha",
    );
  });

  it("switches the metric and reads buckets with the keyboard", async () => {
    render(<TrendPanel members={members} axis={axis} ctx={ctx()} />);
    await userEvent.click(screen.getByRole("button", { name: "Failures" }));
    expect(screen.getByRole("button", { name: "Failures" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const chart = screen.getByRole("group", { name: /Run trend: Failures/ });
    act(() => chart.focus());
    expect(screen.getByText(/^2026-10-03 · .*Total: 0$/u)).toBeInTheDocument();
    await userEvent.keyboard("{Home}");
    expect(
      screen.getByText(/^2026-10-01 · Alpha: 1; .*Total: 7$/u),
    ).toBeInTheDocument();
  });

  it("shows the outage notice instead of an empty chart", () => {
    render(
      <TrendPanel members={members} axis={axis} ctx={ctx("unavailable")} />,
    );
    expect(screen.getByText(copy.agentTeam.runtimeUnavailable)).toBeVisible();
    expect(screen.queryByRole("group", { name: /Run trend:/ })).toBeNull();
  });
});
