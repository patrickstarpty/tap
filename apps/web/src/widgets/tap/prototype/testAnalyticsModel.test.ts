import { describe, expect, it } from "vitest";
import {
  ANALYTICS,
  qualitySummary,
  filterExecutions,
  summarize,
  errorGroups,
  flakyTests,
  executionCsv,
} from "./testAnalyticsModel";
const scope = {
  days: 14,
  plan: "all",
  environment: "all",
  date: "",
  build: "",
};

describe("analytics accounting", () => {
  it("counts logical executions once while preserving the cost and evidence of retries", () => {
    const rows = filterExecutions(scope);
    const stats = summarize(rows);
    expect(rows).toHaveLength(84);
    expect(stats.passed + stats.failed + stats.skipped).toBe(stats.total);
    expect(stats.finalPassed - stats.firstPassed).toBe(stats.recovered);
    expect(stats.attempts).toBe(
      rows.reduce((n, row) => n + row.attempts.length, 0),
    );
    expect(stats.retryMs).toBe(
      rows.reduce(
        (n, row) =>
          n + row.attempts.slice(1).reduce((sum, a) => sum + a.durationMs, 0),
        0,
      ),
    );
    expect(stats.recovered).toBeGreaterThan(0);
    expect(stats.finalRate).toBeGreaterThan(stats.firstRate!);
  });
  it("uses one scope for counts, groups, matrices, and exports; a plan without runs has no invented data", () => {
    expect(filterExecutions({ ...scope, plan: "TP-102" })).toEqual([]);
    const rows = filterExecutions({
      ...scope,
      environment: "staging",
      date: "2026-09-04",
    });
    expect(rows).toHaveLength(3);
    const stats = summarize(rows);
    expect(stats.failed).toBe(1);
    expect(stats.recovered).toBe(1);
    expect(flakyTests(rows)).toHaveLength(1);
    expect(errorGroups(rows).reduce((n, g) => n + g.failedAttempts, 0)).toBe(
      stats.failedAttempts,
    );
    expect(executionCsv(rows).split("\n")).toHaveLength(4);
  });
  it("resolves all immutable snapshot links and shows exactly the actual attempts", () => {
    for (const row of ANALYTICS.executions) {
      expect(ANALYTICS.tests.some((t) => t.id === row.testId)).toBe(true);
      expect(ANALYTICS.builds.some((b) => b.id === row.buildId)).toBe(true);
      for (const attempt of row.attempts) {
        expect(attempt.status === "failed").toBe(attempt.errorId !== null);
        if (attempt.errorId)
          expect(ANALYTICS.errors.some((e) => e.id === attempt.errorId)).toBe(
            true,
          );
      }
    }
    const empty = summarize([]);
    expect(empty.firstRate).toBeNull();
    expect(empty.finalRate).toBeNull();
    expect(flakyTests([])).toEqual([]);
  });
});

it("separates latest observed failures from historical failures and measures scenario execution coverage", () => {
  const rows = filterExecutions(scope);
  const assessment = qualitySummary(rows, "all");
  expect(assessment.latest).toHaveLength(6);
  expect(assessment.latestFailed).toBe(1);
  expect(assessment.executedScenarios).toBe(3);
  expect(assessment.plannedScenarios).toBe(4);
  expect(assessment.missingPlans.map((plan) => plan.id)).toEqual(["TP-102"]);
  const qa = qualitySummary(
    filterExecutions({ ...scope, environment: "qa" }),
    "TP-101",
  );
  expect(qa.latestFailed).toBe(0);
  expect(qa.plannedScenarios).toBe(3);
  const empty = qualitySummary([], "TP-102");
  expect(empty.latest).toHaveLength(0);
  expect(empty.executedScenarios).toBe(0);
  expect(empty.plannedScenarios).toBe(1);
});
