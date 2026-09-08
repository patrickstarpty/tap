import { createInitialArtifactState } from "./artifacts/fixtures";

export interface AnalyticsScope {
  days: number;
  plan: string;
  environment: string;
  date: string;
  build: string;
}
export interface Attempt {
  status: "passed" | "failed";
  durationMs: number;
  errorId: string | null;
  log: string;
}
export interface Execution {
  id: string;
  testId: string;
  buildId: string;
  attempts: readonly Attempt[];
}
export interface Build {
  id: string;
  date: string;
  environment: string;
  commit: string;
  startedAt: string;
}
export interface AnalyticsTest {
  id: string;
  planId: string;
  automationId: string;
  name: string;
  nameZh: string;
}

const snapshot = createInitialArtifactState();
const plan = snapshot.testPlans.find((p) => p.id === "TP-101")!;
const testNamesZh: Readonly<Record<string, string>> = {
  "TP-101-SC-01": "完整投保申请进入核保",
  "TP-101-SC-02": "缺少健康告知时阻止提交",
  "TP-101-SC-03": "高保额申请转人工审核",
};
const tests: AnalyticsTest[] = plan.scenarios.map((scenario) => ({
  id: scenario.id,
  planId: plan.id,
  automationId: plan.automationId!,
  name: scenario.title,
  nameZh: testNamesZh[scenario.id] ?? scenario.title,
}));
const errors = [
  {
    id: "ERR-503",
    name: "Underwriting service unavailable",
    nameZh: "核保服务不可用",
    category: "Environment",
    categoryZh: "环境问题",
    message: "HTTP 503: POST /underwriting/evaluate — service unavailable",
    hypothesis:
      "Matching 503 responses are grouped together. Compare service health and timing across the affected executions before changing assertions.",
    hypothesisZh:
      "相同 503 响应归入同组。建议对照受影响执行的时间与核保服务状态，再判断是否需要修改断言。",
  },
  {
    id: "ERR-422",
    name: "Health disclosure validation mismatch",
    nameZh: "健康告知校验不匹配",
    category: "Product",
    categoryZh: "产品缺陷",
    message:
      "AssertionError: expected HTTP 422 for missing health disclosure, received 200",
    hypothesis:
      "The missing-disclosure scenario passed on the previous revision and fails on the new revision. Check the validation change; this is correlation, not a confirmed cause.",
    hypothesisZh:
      "缺少健康告知场景在前一版本通过，在新版本失败。建议检查校验变更；版本关联尚不等于已确认根因。",
  },
  {
    id: "ERR-WAIT",
    name: "Underwriting status wait exceeded",
    nameZh: "核保状态等待超时",
    category: "Automation",
    categoryZh: "测试脚本",
    message:
      "TimeoutError: locator('[data-testid=underwriting-status]') exceeded 5000ms",
    hypothesis:
      "The first attempt timed out and the retry passed with the same commit and environment. Compare state updates with assertion timing; a retry alone does not establish the cause.",
    hypothesisZh:
      "相同版本和环境下，首次等待超时、重试通过。建议对照状态更新与断言时机；重试通过本身不能确定原因。",
  },
];
const builds: Build[] = [];
const executions: Execution[] = [];
for (let day = 0; day < 14; day++) {
  const date = new Date(Date.UTC(2026, 7, 24 + day)).toISOString().slice(0, 10);
  for (const [envIndex, environment] of ["qa", "staging"].entries()) {
    const build: Build = {
      id: `BUILD-${2840 + day * 2 + envIndex}`,
      date,
      environment,
      commit: day < 9 ? "7b2a190" : "a43c821",
      startedAt: `${date}T${envIndex ? "09" : "08"}:00:00+08:00`,
    };
    builds.push(build);
    tests.forEach((test, index) => {
      const durationMs = 1400 + index * 700 + (day % 4) * 180;
      let errorId: string | null = null;
      let recovered = false;
      if (environment === "staging" && (day === 9 || day === 10) && index !== 1)
        errorId = "ERR-503";
      else if (environment === "staging" && day >= 9 && index === 1)
        errorId = "ERR-422";
      else if (
        environment === "staging" &&
        index === 0 &&
        [6, 8, 11, 12, 13].includes(day)
      ) {
        errorId = "ERR-WAIT";
        recovered = true;
      } else if (
        environment === "qa" &&
        index === 2 &&
        [4, 7, 11].includes(day)
      ) {
        errorId = "ERR-503";
        recovered = true;
      }
      const skipped = environment === "qa" && index === 1 && day === 3;
      const makeAttempt = (error: string | null): Attempt => ({
        status: error ? "failed" : "passed",
        durationMs: error === "ERR-WAIT" ? 5000 : durationMs,
        errorId: error,
        log: [
          "[fixture] Synthetic execution evidence",
          `[context] ${build.id} · ${environment} · ${build.commit}`,
          `[test] ${test.id} · ${test.name}`,
          error
            ? errors.find((e) => e.id === error)!.message
            : `[assertion] ${test.name} — passed`,
        ].join("\n"),
      });
      executions.push({
        id: `${build.id}/${test.id}`,
        testId: test.id,
        buildId: build.id,
        attempts: skipped
          ? []
          : recovered
            ? [makeAttempt(errorId), makeAttempt(null)]
            : [makeAttempt(errorId)],
      });
    });
  }
}

// A separate historical demo snapshot. Never presented as live AutomationRun evidence.
export const ANALYTICS = {
  tests,
  builds,
  executions,
  errors,
  plans: snapshot.testPlans.map(({ id, title }) => ({ id, title })),
  endDate: "2026-09-06",
};
export const getBuild = (row: Execution) =>
  builds.find((b) => b.id === row.buildId)!;
export const getTest = (row: Execution) =>
  tests.find((t) => t.id === row.testId)!;
export const outcome = (row: Execution) =>
  row.attempts.at(-1)?.status ?? "skipped";
export const recovered = (row: Execution) =>
  row.attempts[0]?.status === "failed" && outcome(row) === "passed";
export const retryMs = (row: Execution) =>
  row.attempts.slice(1).reduce((sum, a) => sum + a.durationMs, 0);
export const variantKey = (row: Execution) =>
  `${row.testId}/${getBuild(row).environment}`;
export function filterExecutions(scope: AnalyticsScope): Execution[] {
  const cutoff = new Date(`${ANALYTICS.endDate}T00:00:00Z`);
  cutoff.setUTCDate(cutoff.getUTCDate() - scope.days + 1);
  return executions.filter((row) => {
    const build = getBuild(row);
    return (
      build.date >= cutoff.toISOString().slice(0, 10) &&
      (scope.plan === "all" || getTest(row).planId === scope.plan) &&
      (scope.environment === "all" ||
        build.environment === scope.environment) &&
      (!scope.date || build.date === scope.date) &&
      (!scope.build || build.id === scope.build)
    );
  });
}
export function summarize(rows: readonly Execution[]) {
  const passed = rows.filter((r) => outcome(r) === "passed").length;
  const failed = rows.filter((r) => outcome(r) === "failed").length;
  const skipped = rows.length - passed - failed;
  const firstPassed = rows.filter(
    (r) => r.attempts[0]?.status === "passed",
  ).length;
  const durations = rows
    .filter((r) => r.attempts.length)
    .map((r) => r.attempts.reduce((sum, a) => sum + a.durationMs, 0))
    .sort((a, b) => a - b);
  return {
    total: rows.length,
    passed,
    failed,
    skipped,
    firstPassed,
    finalPassed: passed,
    attempted: passed + failed,
    firstRate: passed + failed ? (firstPassed / (passed + failed)) * 100 : null,
    finalRate: passed + failed ? (passed / (passed + failed)) * 100 : null,
    recovered: rows.filter(recovered).length,
    attempts: rows.reduce((sum, r) => sum + r.attempts.length, 0),
    failedAttempts: rows.reduce(
      (sum, r) => sum + r.attempts.filter((a) => a.status === "failed").length,
      0,
    ),
    retryMs: rows.reduce((sum, r) => sum + retryMs(r), 0),
    p95: durations.length
      ? durations[Math.ceil(durations.length * 0.95) - 1]!
      : null,
  };
}
export function errorGroups(rows: readonly Execution[]) {
  return errors
    .map((error) => {
      const affected = rows.filter((r) =>
        r.attempts.some((a) => a.errorId === error.id),
      );
      return {
        ...error,
        rows: affected,
        failedAttempts: affected.reduce(
          (sum, r) =>
            sum + r.attempts.filter((a) => a.errorId === error.id).length,
          0,
        ),
        tests: new Set(affected.map((r) => r.testId)).size,
        builds: new Set(affected.map((r) => r.buildId)).size,
        recovered: affected.filter(recovered).length,
        firstSeen: affected[0] ? getBuild(affected[0]).date : "",
      };
    })
    .filter((group) => group.rows.length)
    .sort((a, b) => b.failedAttempts - a.failedAttempts);
}
export function flakyTests(rows: readonly Execution[]) {
  const keys = [...new Set(rows.filter(recovered).map(variantKey))];
  return keys
    .map((key) => {
      const history = rows.filter((row) => variantKey(row) === key);
      const stats = summarize(history);
      return {
        key,
        test: getTest(history[0]!),
        environment: getBuild(history[0]!).environment,
        rows: history,
        stats,
        recoveryRate: stats.attempted
          ? (stats.recovered / stats.attempted) * 100
          : 0,
      };
    })
    .sort(
      (a, b) =>
        b.stats.retryMs - a.stats.retryMs ||
        b.stats.recovered - a.stats.recovered,
    );
}
export function dailyRows(rows: readonly Execution[]) {
  return [...new Set(rows.map((r) => getBuild(r).date))].sort().map((date) => ({
    date,
    rows: rows.filter((r) => getBuild(r).date === date),
    stats: summarize(rows.filter((r) => getBuild(r).date === date)),
  }));
}
export function executionCsv(rows: readonly Execution[]) {
  const quote = (value: string | number) =>
    `"${String(value).replaceAll('"', '""')}"`;
  return [
    "Execution,Test,Plan,Build,Date,Environment,Commit,First result,Final result,Attempts,Retry ms,Errors,Data source,Snapshot end",
    ...rows.map((row) =>
      [
        row.id,
        getTest(row).name,
        getTest(row).planId,
        row.buildId,
        getBuild(row).date,
        getBuild(row).environment,
        getBuild(row).commit,
        row.attempts[0]?.status ?? "skipped",
        outcome(row),
        row.attempts.length,
        retryMs(row),
        row.attempts
          .map((a) => a.errorId)
          .filter(Boolean)
          .join("|"),
        "Synthetic demo fixture",
        ANALYTICS.endDate,
      ]
        .map(quote)
        .join(","),
    ),
  ].join("\n");
}

export function qualitySummary(rows: readonly Execution[], planId: string) {
  const latestByVariant = new Map<string, Execution>();
  for (const row of rows) {
    const key = variantKey(row);
    const previous = latestByVariant.get(key);
    if (
      !previous ||
      Date.parse(getBuild(row).startedAt) >
        Date.parse(getBuild(previous).startedAt)
    )
      latestByVariant.set(key, row);
  }
  const latest = [...latestByVariant.values()];
  const plans = snapshot.testPlans.filter(
    (plan) => planId === "all" || plan.id === planId,
  );
  const executedIds = new Set(
    rows.filter((row) => row.attempts.length > 0).map((row) => row.testId),
  );
  const plannedIds = plans.flatMap((plan) =>
    plan.scenarios.map((scenario) => scenario.id),
  );
  return {
    latest,
    latestFailed: latest.filter((row) => outcome(row) === "failed").length,
    plannedScenarios: plannedIds.length,
    executedScenarios: plannedIds.filter((id) => executedIds.has(id)).length,
    missingPlans: plans.filter((plan) =>
      plan.scenarios.some((scenario) => !executedIds.has(scenario.id)),
    ),
  };
}
