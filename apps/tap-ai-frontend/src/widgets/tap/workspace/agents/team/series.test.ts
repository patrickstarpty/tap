import { describe, expect, it } from "vitest";

import { bucketAxis } from "./buckets";
import { deltaOf, deltaTone } from "./delta";
import {
  activeSeries,
  downsample,
  niceScale,
  runRanks,
  splitTop,
  sumRows,
  teamSeries,
} from "./series";

function row(agentKey: string, runs: number | null, trend: [string, number][]) {
  return {
    agentKey,
    name: agentKey,
    runs,
    failures: runs === null ? null : 1,
    costUsd: runs === null ? null : "0.5",
    latencyTotalMs: runs === null ? null : 1000,
    trend: trend.map(([bucketStart, value]) => ({
      agentKey,
      bucketStart,
      runs: value,
      failures: 0,
      costUsd: (value / 10).toFixed(2),
      latencyTotalMs: value * 100,
    })),
  } as never;
}

describe("niceScale", () => {
  it("rounds the top to a clean step and never splits counts", () => {
    expect(niceScale(59, true)).toEqual({ top: 60, ticks: [0, 20, 40, 60] });
    expect(niceScale(3, true)).toEqual({ top: 3, ticks: [0, 1, 2, 3] });
    expect(niceScale(12, true).top).toBe(15);
    expect(niceScale(0.42, false)).toEqual({
      top: 0.6,
      ticks: [0, 0.2, 0.4, 0.6],
    });
    expect(niceScale(0, true)).toEqual({ top: 1, ticks: [0, 1] });
  });
});

describe("team series", () => {
  const axis = bucketAxis({ from: "2026-10-01", to: "2026-10-03" }, "day");
  const rows = [
    row("a", 4, [
      ["2026-10-01", 1],
      ["2026-10-03", 3],
    ]),
    row("b", 2, [["2026-10-01", 2]]),
    row("c", null, []),
  ];

  it("sums members per bucket with zero-filled gaps", () => {
    expect(teamSeries(rows, axis, "runs")).toEqual([3, 0, 3]);
    expect(
      teamSeries(rows, axis, "cost").map((v) => Number(v.toFixed(2))),
    ).toEqual([0.3, 0, 0.3]);
    expect(activeSeries(rows, axis)).toEqual([2, 0, 1]);
  });

  it("sums totals but keeps unknown as unknown", () => {
    expect(sumRows(rows, "failures")).toBe(2);
    expect(sumRows([rows[2]], "failures")).toBeNull();
  });

  it("splits the busiest Agents from the rest", () => {
    const { top, rest } = splitTop(rows, 1);
    expect(top.map((r) => r.agentKey)).toEqual(["a"]);
    expect(rest.map((r) => r.agentKey)).toEqual(["b", "c"]);
  });

  it("ranks by runs with shared ranks and no rank without runs", () => {
    const ranks = runRanks([
      row("x", 5, []),
      row("y", 5, []),
      row("z", 1, []),
      row("w", 0, []),
    ]);
    expect([...ranks]).toEqual([
      ["x", 1],
      ["y", 1],
      ["z", 3],
    ]);
  });

  it("downsamples by summing or taking the peak of neighbours", () => {
    expect(downsample([1, 2, 3, 4, 5], 3)).toEqual([3, 7, 5]);
    expect(downsample([1, 2, 3, 4, 5], 3, "max")).toEqual([2, 4, 5]);
    expect(downsample([1, 2], 3)).toEqual([1, 2]);
  });
});

describe("deltas", () => {
  it("needs a non-zero baseline", () => {
    expect(deltaOf(5, 0)).toBeNull();
    expect(deltaOf(null, 3)).toBeNull();
    expect(deltaOf(120, 100)).toEqual({
      diff: 20,
      percent: 20,
      direction: "up",
    });
  });

  it("colours by whether the change is good news", () => {
    const up = deltaOf(2, 1);
    const down = deltaOf(1, 2);
    expect(deltaTone(up, "up-good")).toBe("good");
    expect(deltaTone(down, "up-good")).toBe("bad");
    expect(deltaTone(up, "up-bad")).toBe("bad");
    expect(deltaTone(down, "up-bad")).toBe("good");
    expect(deltaTone(up, "neutral")).toBe("neutral");
    expect(deltaTone(null, "up-good")).toBe("none");
  });
});
