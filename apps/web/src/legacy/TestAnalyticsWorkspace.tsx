import {
  CloseOutlined,
  DownloadOutlined,
  FilterOutlined,
  MoreOutlined,
  PlusOutlined,
  ShareAltOutlined,
} from "@ant-design/icons";
import { useMemo, useRef, useState, type ReactNode } from "react";
import { AccessibleDialog } from "./AccessibleDialog";
import type { Locale } from "./model";
import {
  ANALYTICS,
  ANALYTICS_PROJECT,
  dailyRows,
  executionCsv,
  filterExecutions,
  getBuild,
  getTest,
  outcome,
  recovered,
  summarize,
  type AnalyticsScope,
  type Execution,
} from "./testAnalyticsModel";
import "./TestAnalyticsWorkspace.css";

import {
  ReportIntakePrototype,
  displayExecutionLog,
  FailureKnowledgeExplanation,
  type ReportFormat,
  type ReportScenario,
} from "./ReportIntakePrototype";

type Breakdown = "stability" | "flakiness" | null;

const RANGE_OPTIONS = [
  { label: "1D", days: 1 },
  { label: "7D", days: 7 },
  { label: "30D", days: 30 },
  { label: "6M", days: 180 },
  { label: "1Y", days: 365 },
  { label: "2Y", days: 730 },
] as const;

function ChartFrame({
  children,
  startLabel,
  endLabel,
}: {
  children: ReactNode;
  startLabel?: string;
  endLabel?: string;
}) {
  return (
    <svg aria-hidden="true" className="bs-chart" viewBox="0 0 520 210">
      <g className="bs-chart-grid">
        <path d="M42 18H505M42 58H505M42 98H505M42 138H505M42 178H505" />
        <path d="M42 18V178M135 18V178M228 18V178M321 18V178M414 18V178M505 18V178" />
      </g>
      <g className="bs-chart-axis">
        <text x="8" y="22">
          100
        </text>
        <text x="17" y="62">
          75
        </text>
        <text x="17" y="102">
          50
        </text>
        <text x="17" y="142">
          25
        </text>
        <text x="25" y="182">
          0
        </text>
        <text x="50" y="204">
          {startLabel ?? "24 Aug"}
        </text>
        <text x="244" y="204">
          {startLabel ? "" : "31 Aug"}
        </text>
        <text x="448" y="204">
          {endLabel ?? "6 Sep"}
        </text>
      </g>
      {children}
    </svg>
  );
}

function points(values: readonly number[], max = 100) {
  return values
    .map(
      (value, index) =>
        `${42 + (index / Math.max(values.length - 1, 1)) * 463},${178 - (value / max) * 160}`,
    )
    .join(" ");
}

function Widget({
  title,
  children,
  className = "",
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <article className={`bs-widget ${className}`}>
      <header className="bs-widget-header">
        <h2>{title}</h2>
        <span aria-hidden="true" className="bs-widget-more">
          <MoreOutlined />
        </span>
      </header>
      {children}
    </article>
  );
}

export function TestAnalyticsWorkspace({
  locale = "zh",
  initialPlanId,
  reviewPrototype = false,
}: {
  locale?: Locale;
  initialPlanId?: string;
  reviewPrototype?: boolean;
}) {
  const [receivedReports, setReceivedReports] = useState<
    Record<string, ReportFormat>
  >({});
  const t = (zh: string, en: string) => (locale === "zh" ? zh : en);
  const [scope, setScope] = useState<AnalyticsScope>({
    days: 30,
    project: ANALYTICS_PROJECT.id,
    branch: "all",
    plan: initialPlanId ?? "all",
    environment: "all",
    date: "",
    build: "",
  });
  const reportFormat = receivedReports[scope.build] ?? "allure";

  const reportScenario: ReportScenario = receivedReports[scope.build]
    ? "ready"
    : scope.build === "BUILD-2868"
      ? "missing"
      : scope.build === "BUILD-2869"
        ? "invalid"
        : "ready";
  const blocked = reviewPrototype && reportScenario !== "ready";
  const [draftScope, setDraftScope] = useState(scope);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [breakdown, setBreakdown] = useState<Breakdown>(null);
  const [selected, setSelected] = useState<Execution | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const rows = useMemo(() => {
    const filtered = filterExecutions(scope).map((row) =>
      reviewPrototype && receivedReports[row.buildId] === "junit"
        ? { ...row, attempts: row.attempts.slice(-1) }
        : row,
    );
    return filtered;
  }, [scope, reviewPrototype, receivedReports]);
  const retriesAvailable =
    !reviewPrototype ||
    !rows.some((row) => receivedReports[row.buildId] === "junit");
  const projectBuilds = [
    ...ANALYTICS.builds,
    ...ANALYTICS.pendingBuilds,
  ].filter((build) => build.projectId === scope.project);
  const availableBuilds = projectBuilds.filter(
    (build) =>
      (scope.environment === "all" ||
        build.environment === scope.environment) &&
      (scope.branch === "all" || build.branch === scope.branch) &&
      (!scope.date || build.date === scope.date) &&
      (!scope.build || build.id === scope.build) &&
      (scope.plan === "all" || scope.plan === "TP-101") &&
      (new Date(`${ANALYTICS.endDate}T00:00:00Z`).getTime() -
        new Date(`${build.date}T00:00:00Z`).getTime()) /
        86400000 <
        scope.days,
  );
  const stats = summarize(rows);
  const daily = dailyRows(rows);
  const stabilityValues = daily.map((day) => day.stats.firstRate ?? 0);
  const flakinessValues = daily.map((day) =>
    day.stats.attempted ? (day.stats.recovered / day.stats.attempted) * 100 : 0,
  );
  const stability = stats.attempted
    ? (stats.firstPassed / stats.attempted) * 100
    : 0;
  const flakiness = stats.attempted
    ? (stats.recovered / stats.attempted) * 100
    : 0;
  const failureRate = stats.attempted
    ? (stats.failed / stats.attempted) * 100
    : 0;
  const buildRows = ANALYTICS.builds
    .filter((build) => rows.some((row) => row.buildId === build.id))
    .slice(-9)
    .reverse()
    .map((build) => {
      const executions = rows.filter((row) => row.buildId === build.id);
      return {
        build,
        passed: executions.filter((row) => outcome(row) === "passed").length,
        failed: executions.filter((row) => outcome(row) === "failed").length,
        skipped: executions.filter((row) => outcome(row) === "skipped").length,
      };
    });
  const openFrom = (element: HTMLElement) => {
    opener.current = element;
  };
  const download = () => {
    if (blocked) return;
    const url = URL.createObjectURL(
      new Blob([executionCsv(rows, retriesAvailable)], {
        type: "text/csv;charset=utf-8",
      }),
    );
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `test-analytics-synthetic-${ANALYTICS.endDate}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  const breakdownRows =
    breakdown === "flakiness"
      ? rows.filter(recovered)
      : rows.filter((row) => outcome(row) === "passed" && !recovered(row));

  return (
    <section
      className={`bs-dashboard${reviewPrototype ? " bs-dashboard--product" : ""}`}
    >
      <header className="bs-page-header">
        <div>
          {!reviewPrototype ? (
            <nav aria-label={t("面包屑", "Breadcrumb")}>
              <span>Dashboards</span>
            </nav>
          ) : null}
          <h1>
            {t(
              reviewPrototype ? "Test Insights" : "演示仪表盘",
              reviewPrototype ? "Test Insights" : "Demo Dashboard",
            )}
          </h1>
          {reviewPrototype ? (
            <p className="bs-project-context">
              <span>{t("项目", "Project")}</span>{" "}
              <strong>
                {t(ANALYTICS_PROJECT.nameZh, ANALYTICS_PROJECT.name)}
              </strong>
            </p>
          ) : null}
        </div>
        <div className="bs-page-actions">
          <button
            aria-label={t("下载仪表盘", "Download dashboard")}
            disabled={blocked}
            onClick={download}
          >
            <DownloadOutlined />
          </button>
          <button
            aria-label={t("分享", "Share")}
            className="bs-share-button"
            onClick={(event) => {
              openFrom(event.currentTarget);
              setShareOpen(true);
            }}
          >
            <ShareAltOutlined /> {t("分享", "Share")}
          </button>
          <span aria-hidden="true">
            <MoreOutlined />
          </span>
        </div>
      </header>

      {reviewPrototype ? (
        <ReportIntakePrototype
          locale={locale}
          format={reportFormat}
          scenario={reportScenario}
          receivedBuilds={Object.keys(receivedReports)}
          builds={projectBuilds}
          scopedBuilds={availableBuilds}
          projectName={t(ANALYTICS_PROJECT.nameZh, ANALYTICS_PROJECT.name)}
          buildId={scope.build}
          onBuild={(build) => setScope({ ...scope, build, date: "" })}
          onImport={(format, buildId) => {
            setReceivedReports((reports) => ({
              ...reports,
              [buildId]: format,
            }));
          }}
        />
      ) : null}
      <>
        <div className="bs-toolbar">
          <div className="bs-range" aria-label={t("时间范围", "Date range")}>
            {RANGE_OPTIONS.map((option) => (
              <button
                key={option.label}
                aria-pressed={scope.days === option.days}
                onClick={() => setScope({ ...scope, days: option.days })}
              >
                {option.label}
              </button>
            ))}
            <button aria-pressed="false">{t("自定义", "Custom")}</button>
          </div>
          <div className="bs-toolbar-actions">
            <button
              aria-label={t("筛选", "Filters")}
              className="bs-filter-button"
              onClick={(event) => {
                openFrom(event.currentTarget);
                setDraftScope(scope);
                setFiltersOpen(true);
              }}
            >
              <FilterOutlined />
            </button>
            <button
              aria-label={t("添加组件", "Add Widgets")}
              className="bs-add-button"
              onClick={(event) => {
                openFrom(event.currentTarget);
                setCatalogOpen(true);
              }}
            >
              <PlusOutlined /> {t("添加组件", "Add Widgets")}
            </button>
          </div>
        </div>

        {scope.plan !== "all" ? (
          <div className="bs-filter-chip">
            {t("测试计划", "Test Plan")}: {scope.plan}
            <button
              aria-label={t("清除测试计划筛选", "Clear Test Plan filter")}
              onClick={() => setScope({ ...scope, plan: "all" })}
            >
              <CloseOutlined />
            </button>
          </div>
        ) : null}

        {scope.environment !== "all" ? (
          <div className="bs-filter-chip">
            {t("环境", "Environment")}:{" "}
            {scope.environment === "staging" ? "Staging" : "QA"}
            <button
              aria-label={t("清除环境筛选", "Clear environment filter")}
              onClick={() => setScope({ ...scope, environment: "all" })}
            >
              <CloseOutlined />
            </button>
          </div>
        ) : null}

        {reviewPrototype && scope.branch !== "all" ? (
          <div className="bs-filter-chip">
            {t("分支", "Branch")}: {scope.branch}
            <button
              aria-label={t("清除分支筛选", "Clear branch filter")}
              onClick={() => setScope({ ...scope, branch: "all" })}
            >
              <CloseOutlined />
            </button>
          </div>
        ) : null}
        {!blocked && reviewPrototype ? (
          <details className="bs-metric-definitions">
            <summary>{t("指标说明", "Metric definitions")}</summary>
            <dl>
              <dt>{t("执行数", "Executions")}</dt>
              <dd>
                {t(
                  "同一构建内，用例标识 + 参数去重；重试属于同一次执行。",
                  "Unique test and parameter combinations within a build. Retries belong to the same execution.",
                )}
              </dd>
              <dt>{t("最终通过率", "Final pass rate")}</dt>
              <dd>
                {t(
                  `最终通过 ${stats.passed} / 已执行 ${stats.attempted}；跳过 ${stats.skipped} 不进入分母。`,
                  `Finally passed ${stats.passed} / executed ${stats.attempted}; ${stats.skipped} skipped tests are excluded.`,
                )}
              </dd>
              <dt>{t("失败率", "Failure rate")}</dt>
              <dd>
                {t(
                  `最终失败 ${stats.failed} / 已执行 ${stats.attempted}。错误与断言失败均计为失败。`,
                  `Finally failed ${stats.failed} / executed ${stats.attempted}. Includes errors and assertion failures.`,
                )}
              </dd>
              <dt>{t("首次通过率", "First-pass rate")}</dt>
              <dd>
                {t(
                  "首次通过 / 已执行；需要完整尝试记录。",
                  "Passed on the first attempt / executed tests. Requires complete attempt history.",
                )}
              </dd>
              <dt>{t("重试恢复占比", "Retry recovery share")}</dt>
              <dd>
                {t(
                  "首次失败、最终通过 / 已执行；不能等同于跨构建的 flaky 判定。",
                  "Initially failed but finally passed / executed tests. This does not establish flakiness across builds.",
                )}
              </dd>
              <dt>{t("P95 执行耗时", "P95 execution duration")}</dt>
              <dd>
                {t(
                  "每次执行的所有尝试耗时之和，按最近秩取第 95 百分位；不是构建墙钟时长。",
                  "The nearest-rank 95th percentile of summed attempt durations for each execution, rather than build wall-clock time.",
                )}
              </dd>
              <dt>{t("趋势与维度", "Trends and dimensions")}</dt>
              <dd>
                {t(
                  "至少 2 个有数据的日期才展示按日趋势。浏览器维度以报告提供的信息为准。",
                  "Daily trends need at least 2 dates with data. Browser dimensions depend on the report.",
                )}
              </dd>
            </dl>
          </details>
        ) : null}
        {!blocked ? (
          <section
            aria-label={t("仪表盘组件", "Dashboard widgets")}
            className="bs-widget-grid"
          >
            <Widget title={t("摘要", "Summary")} className="bs-summary-widget">
              <div className="bs-summary-row">
                <div>
                  <span>{t("测试摘要", "Tests Summary")}</span>
                  <strong data-testid="tests-summary-value">
                    {stats.total}
                  </strong>
                  <i className="bs-summary-bar bs-summary-bar--tests" />
                </div>
                <div>
                  <span>{t("失败率", "Failure Rate")}</span>
                  <strong>
                    {reviewPrototype && !stats.attempted
                      ? "—"
                      : `${failureRate.toFixed(2)}%`}
                  </strong>
                </div>
                <div>
                  <span>{t("失败数量", "Failure Count")}</span>
                  <strong>{stats.failed}</strong>
                  <i className="bs-summary-bar bs-summary-bar--failures" />
                </div>
                <div>
                  <span>
                    {t(
                      reviewPrototype ? "首次通过率" : "稳定性",
                      reviewPrototype ? "First-pass rate" : "Stability",
                    )}
                  </span>
                  <strong>
                    {retriesAvailable && stats.attempted
                      ? `${stability.toFixed(2)}%`
                      : "—"}
                  </strong>
                </div>
                <div>
                  <span>
                    {t(
                      reviewPrototype ? "重试恢复占比" : "不稳定性",
                      reviewPrototype ? "Retry recovery share" : "Flakiness",
                    )}
                  </span>
                  <strong>
                    {retriesAvailable && stats.attempted
                      ? `${flakiness.toFixed(2)}%`
                      : "—"}
                  </strong>
                  <small>
                    {retriesAvailable ? stats.recovered : "—"} /{" "}
                    {stats.attempted}{" "}
                    {t(
                      retriesAvailable
                        ? "次执行经重试恢复"
                        : "（报告未提供重试记录，指标不可用）",
                      reviewPrototype
                        ? retriesAvailable
                          ? "executions recovered after retry"
                          : "(retry history unavailable)"
                        : "test executions are flaky",
                    )}
                  </small>
                </div>
                <div>
                  <span>
                    {t(
                      reviewPrototype ? "最终通过率" : "覆盖平台",
                      reviewPrototype ? "Final pass rate" : "Platforms Covered",
                    )}
                  </span>
                  <strong>
                    {reviewPrototype
                      ? stats.finalRate === null
                        ? "—"
                        : `${stats.finalRate.toFixed(2)}%`
                      : 6}
                  </strong>
                </div>
              </div>
            </Widget>

            <Widget
              title={t("构建摘要", "Build Summary")}
              className="bs-build-summary"
            >
              <div className="bs-legend">
                <span>
                  <i className="is-green" />
                  {t("通过", "passed")}
                </span>
                <span>
                  <i className="is-red" />
                  {t("失败", "failed")}
                </span>
                <span>
                  <i className="is-grey" />
                  {t("跳过", "skipped")}
                </span>
                <span>
                  <i className="is-yellow" />
                  {t("未知", "unknown")}
                </span>
              </div>
              <div className="bs-build-bars">
                {buildRows.map(({ build, passed, failed, skipped }) => (
                  <div key={build.id}>
                    <span title={build.id}>{build.id.toLowerCase()}</span>
                    <i>
                      <b
                        className="is-green"
                        style={{
                          width: reviewPrototype
                            ? `${(passed / 15) * 100}%`
                            : `${passed * 26}px`,
                        }}
                      />
                      <b
                        className="is-red"
                        style={{
                          width: reviewPrototype
                            ? `${(failed / 15) * 100}%`
                            : `${failed * 26}px`,
                        }}
                      />
                      <b
                        className="is-grey"
                        style={{
                          width: reviewPrototype
                            ? `${(skipped / 15) * 100}%`
                            : `${skipped * 26}px`,
                        }}
                      />
                    </i>
                  </div>
                ))}
              </div>
              <div className="bs-x-axis">
                <span>0</span>
                <span>5</span>
                <span>10</span>
                <span>15</span>
              </div>
            </Widget>

            <Widget
              title={t(
                reviewPrototype ? "首次通过率" : "稳定性",
                reviewPrototype ? "First-pass rate" : "Stability",
              )}
              className="bs-stability-widget"
            >
              {reviewPrototype ? (
                !retriesAvailable ? (
                  <p className="bs-prototype-stat">
                    {t(
                      "缺少尝试记录，首次通过率不可用。",
                      "First-pass rate is unavailable: attempt history is missing.",
                    )}
                  </p>
                ) : daily.length < 2 ? (
                  <p className="bs-prototype-stat">
                    {t(
                      `数据不足：按日趋势需要至少 2 个有数据的日期，当前仅 ${daily.length} 天。不展示趋势。`,
                      `Daily trends need at least 2 dates with data; ${daily.length} available.`,
                    )}
                  </p>
                ) : (
                  <>
                    <p className="bs-prototype-stat">
                      {t("全部执行", "All executions")} · {daily[0]?.date}{" "}
                      {t("至", "to")} {daily.at(-1)?.date}
                    </p>
                    <ChartFrame
                      startLabel={daily[0]?.date.slice(5)}
                      endLabel={daily.at(-1)?.date.slice(5)}
                    >
                      <polyline
                        className="bs-line is-blue"
                        points={points(stabilityValues)}
                      />
                    </ChartFrame>
                  </>
                )
              ) : (
                <>
                  <div className="bs-legend bs-chart-legend">
                    <span>
                      <i className="is-blue" />
                      Overall
                    </span>
                    <span>
                      <i className="is-green" />
                      Regression
                    </span>
                    <span>
                      <i className="is-purple" />
                      Sanity
                    </span>
                    <span>
                      <i className="is-orange" />
                      API tests
                    </span>
                  </div>
                  <button
                    aria-label={t("查看稳定性明细", "View Stability breakdown")}
                    className="bs-chart-button"
                    onClick={(event) => {
                      openFrom(event.currentTarget);
                      setBreakdown("stability");
                    }}
                  >
                    <ChartFrame>
                      <polyline
                        className="bs-line is-blue"
                        points={points(stabilityValues)}
                      />
                      <polyline
                        className="bs-line is-green"
                        points={points(
                          stabilityValues.map((v) => Math.min(100, v + 6)),
                        )}
                      />
                      <polyline
                        className="bs-line is-purple"
                        points={points(
                          stabilityValues.map((v) => Math.max(0, v - 7)),
                        )}
                      />
                      <polyline
                        className="bs-line is-orange"
                        points={points(
                          stabilityValues.map((v) => Math.max(0, v - 19)),
                        )}
                      />
                    </ChartFrame>
                  </button>
                </>
              )}
            </Widget>

            <Widget
              title={t(
                reviewPrototype ? "重试恢复占比" : "不稳定性",
                reviewPrototype ? "Retry recovery share" : "Flakiness",
              )}
              className="bs-flakiness-widget"
            >
              {reviewPrototype ? (
                <div className="bs-prototype-stat">
                  <strong>
                    {retriesAvailable && stats.attempted
                      ? `${flakiness.toFixed(2)}%`
                      : "—"}
                  </strong>
                  {retriesAvailable
                    ? t(
                        `${stats.recovered} / ${stats.attempted} 次已执行用例经重试恢复。`,
                        `${stats.recovered} / ${stats.attempted} executions recovered after retry.`,
                      )
                    : t("缺少重试记录。", "Retry history is unavailable.")}
                  {retriesAvailable && stats.recovered > 0 ? (
                    <button
                      className="bs-add-button"
                      onClick={(event) => {
                        openFrom(event.currentTarget);
                        setBreakdown("flakiness");
                      }}
                    >
                      {t("查看恢复明细", "View recovered executions")}
                    </button>
                  ) : null}
                </div>
              ) : (
                <>
                  <div className="bs-chart-summary">
                    <span>
                      {t(
                        reviewPrototype ? "重试恢复占比" : "平均不稳定性",
                        reviewPrototype
                          ? "Retry recovery share"
                          : "Average Flakiness",
                      )}
                    </span>
                    <strong>
                      {retriesAvailable && stats.attempted
                        ? `${flakiness.toFixed(2)}%`
                        : "—"}
                    </strong>
                    <small>
                      {retriesAvailable ? stats.recovered : "—"}/
                      {stats.attempted}{" "}
                      {t(
                        retriesAvailable
                          ? "次执行经重试恢复"
                          : "（报告未提供重试记录，指标不可用）",
                        reviewPrototype
                          ? retriesAvailable
                            ? "executions recovered after retry"
                            : "(retry history unavailable)"
                          : "test executions are flaky",
                      )}
                    </small>
                  </div>
                  <div className="bs-legend">
                    <span>
                      <i className="is-blue" />
                      Flakiness
                    </span>
                  </div>
                  <button
                    aria-label={t(
                      "查看不稳定性明细",
                      "View Flakiness breakdown",
                    )}
                    className="bs-chart-button"
                    onClick={(event) => {
                      openFrom(event.currentTarget);
                      setBreakdown("flakiness");
                    }}
                  >
                    <ChartFrame>
                      <polyline
                        className="bs-line is-blue"
                        points={points(
                          flakinessValues,
                          Math.max(...flakinessValues, 12),
                        )}
                      />
                    </ChartFrame>
                  </button>
                </>
              )}
            </Widget>

            <Widget
              title={t("浏览器摘要", "Browser wise summary")}
              className="bs-browser-widget"
            >
              {reviewPrototype ? (
                <p className="bs-prototype-stat">
                  {t(
                    "报告未提供浏览器或设备信息，此维度暂不可用。",
                    "Browser and device information is unavailable in this report.",
                  )}
                </p>
              ) : (
                <>
                  <div className="bs-browser-grid">
                    {[
                      ["Chrome 133", 28, 82.14],
                      ["NA", 14, 92.86],
                      ["Chrome 134", 14, 78.57],
                      ["Edge 133", 14, 85.71],
                      ["Safari 14", 7, 71.43],
                      ["Firefox 135", 7, 85.71],
                    ].map(([browser, runs, rate]) => (
                      <div key={String(browser)}>
                        <strong>{browser}</strong>
                        <span>
                          {t("测试运行", "Test Runs")}
                          <b>{runs}</b>
                        </span>
                        <span>
                          {t(
                            reviewPrototype ? "首次通过率" : "稳定性",
                            reviewPrototype ? "First-pass rate" : "Stability",
                          )}
                          <b>{rate}</b>
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </Widget>

            <Widget
              title={t(
                reviewPrototype ? "执行耗时" : "构建性能",
                reviewPrototype ? "Execution duration" : "Build Performance",
              )}
              className="bs-performance-widget"
            >
              {reviewPrototype ? (
                <div className="bs-prototype-stat">
                  <strong>
                    {stats.p95 === null
                      ? "—"
                      : `${(stats.p95 / 1000).toFixed(2)} s`}
                  </strong>
                  {t("P95 执行耗时", "P95 execution duration")} ·{" "}
                  {!retriesAvailable
                    ? t(
                        "仅包含报告提供的最终尝试耗时",
                        "Final attempt durations from the report",
                      )
                    : t(
                        "包含已记录的尝试耗时",
                        "Includes recorded attempt durations",
                      )}
                </div>
              ) : (
                <>
                  <div className="bs-legend bs-chart-legend">
                    <span>
                      <i className="is-blue" />
                      All Builds: Average Duration
                    </span>
                    <span>
                      <i className="is-green" />
                      Nightly: Average Duration
                    </span>
                    <span>
                      <i className="is-grey" />
                      All Builds: Test Executions
                    </span>
                    <span>
                      <i className="is-orange" />
                      Nightly: Test Executions
                    </span>
                  </div>
                  <ChartFrame>
                    {daily.slice(-8).map((day, index) => (
                      <g key={day.date}>
                        <rect
                          className="bs-column is-blue"
                          height={40 + (index % 3) * 9}
                          width="12"
                          x={65 + index * 55}
                          y={138 - (index % 3) * 9}
                        />
                        <rect
                          className="bs-column is-green"
                          height={82 + (index % 4) * 7}
                          width="12"
                          x={79 + index * 55}
                          y={96 - (index % 4) * 7}
                        />
                      </g>
                    ))}
                    <polyline
                      className="bs-line is-orange"
                      points="65,62 120,64 175,61 230,63 285,60 340,62 395,59 450,61"
                    />
                  </ChartFrame>
                </>
              )}
            </Widget>
            {reviewPrototype ? (
              <Widget
                title={t(
                  `失败执行 · ${stats.failed}`,
                  `Failed executions · ${stats.failed}`,
                )}
                className="bs-failure-list"
              >
                {rows
                  .filter((row) => outcome(row) === "failed")
                  .map((row) => (
                    <button
                      key={row.id}
                      onClick={(event) => {
                        openFrom(event.currentTarget);
                        setSelected(row);
                      }}
                    >
                      <span>
                        <strong>
                          {locale === "zh"
                            ? getTest(row).nameZh
                            : getTest(row).name}
                        </strong>
                        <small>
                          {row.buildId} · {getBuild(row).environment} ·{" "}
                          {displayExecutionLog(row.attempts.at(-1)?.log)}
                        </small>
                      </span>
                      <span>{t("查看失败 →", "View failure →")}</span>
                    </button>
                  ))}
                {stats.failed === 0 ? (
                  <p className="bs-prototype-stat">
                    {t(
                      "当前筛选范围没有失败执行。",
                      "No failed executions match these filters.",
                    )}
                  </p>
                ) : null}
              </Widget>
            ) : null}
          </section>
        ) : null}
      </>
      {filtersOpen ? (
        <AccessibleDialog
          ariaLabel={t("仪表盘筛选", "Dashboard filters")}
          className={`bs-side-panel${reviewPrototype ? " bs-side-panel--review" : ""}`}
          onClose={() => setFiltersOpen(false)}
          opener={opener.current}
        >
          <header>
            <div>
              <h2>{t("筛选", "Filters")}</h2>
              <p>
                {t(
                  "筛选条件会应用到所有组件",
                  "Filters apply to all dashboard widgets",
                )}
              </p>
            </div>
            <button
              aria-label={t("关闭筛选", "Close filters")}
              onClick={() => setFiltersOpen(false)}
            >
              <CloseOutlined />
            </button>
          </header>
          <label>
            {t("环境", "Environment")}
            <select
              aria-label={t("环境", "Environment")}
              value={draftScope.environment}
              onChange={(event) =>
                setDraftScope({
                  ...draftScope,
                  environment: event.target.value,
                })
              }
            >
              <option value="all">{t("全部环境", "All environments")}</option>
              <option value="qa">QA</option>
              <option value="staging">Staging</option>
            </select>
          </label>
          {reviewPrototype ? (
            <label>
              {t("分支", "Branch")}
              <select
                aria-label={t("分支", "Branch")}
                value={draftScope.branch}
                onChange={(event) =>
                  setDraftScope({ ...draftScope, branch: event.target.value })
                }
              >
                <option value="all">{t("全部分支", "All branches")}</option>
                {[...new Set(projectBuilds.map((build) => build.branch))].map(
                  (branch) => (
                    <option key={branch}>{branch}</option>
                  ),
                )}
              </select>
            </label>
          ) : null}
          <label>
            {t("测试计划", "Test plan")}
            <select
              aria-label={t("测试计划", "Test plan")}
              value={draftScope.plan}
              onChange={(event) =>
                setDraftScope({ ...draftScope, plan: event.target.value })
              }
            >
              <option value="all">{t("全部测试计划", "All test plans")}</option>
              <option value="TP-101">TP-101</option>
            </select>
          </label>
          <footer>
            <button onClick={() => setFiltersOpen(false)}>
              {t("取消", "Cancel")}
            </button>
            <button
              className="bs-add-button"
              onClick={() => {
                setScope(draftScope);
                setFiltersOpen(false);
              }}
            >
              {t("应用", "Apply")}
            </button>
          </footer>
        </AccessibleDialog>
      ) : null}

      {catalogOpen ? (
        <AccessibleDialog
          ariaLabel={t("添加组件", "Add Widgets")}
          className="bs-widget-catalog"
          onClose={() => setCatalogOpen(false)}
          opener={opener.current}
        >
          <header>
            <div>
              <h2>{t("添加组件", "Add Widgets")}</h2>
              <p>{t("选择组件", "Choose a widget")}</p>
            </div>
            <button
              aria-label={t("关闭组件目录", "Close widget catalog")}
              onClick={() => setCatalogOpen(false)}
            >
              <CloseOutlined />
            </button>
          </header>
          <div className="bs-catalog-grid">
            {[
              "Build Summary",
              reviewPrototype
                ? t("首次通过率", "First-pass rate")
                : "Stability",
              reviewPrototype
                ? t("重试恢复占比", "Retry recovery share")
                : "Flakiness",
              "Component Summary",
              reviewPrototype
                ? t("执行耗时", "Execution duration")
                : "Build Performance",
              "Platform Coverage",
            ].map((name) => (
              <article key={name}>
                <div className="bs-catalog-preview">
                  <svg viewBox="0 0 120 45">
                    <path d="M4 36L25 28L47 31L69 13L91 22L116 7" />
                  </svg>
                </div>
                <h3>{name}</h3>
                <button disabled>{t("已添加", "Added")}</button>
              </article>
            ))}
          </div>
        </AccessibleDialog>
      ) : null}

      {shareOpen ? (
        <AccessibleDialog
          ariaLabel={t("分享仪表盘", "Share dashboard")}
          className="bs-share-dialog"
          onClose={() => setShareOpen(false)}
          opener={opener.current}
        >
          <header>
            <h2>{t("分享仪表盘", "Share dashboard")}</h2>
            <button
              aria-label={t("关闭分享", "Close share")}
              onClick={() => setShareOpen(false)}
            >
              <CloseOutlined />
            </button>
          </header>
          <p>
            {t(
              reviewPrototype
                ? "拥有链接的团队成员可以查看此仪表盘。"
                : "拥有链接的团队成员可以查看此演示仪表盘。",
              reviewPrototype
                ? "Team members with the link can view this dashboard."
                : "Team members with the link can view this demo dashboard.",
            )}
          </p>
          <button className="bs-add-button" onClick={() => setShareOpen(false)}>
            {t("复制链接", "Copy link")}
          </button>
        </AccessibleDialog>
      ) : null}

      {breakdown ? (
        <AccessibleDialog
          ariaLabel={`${breakdown === "flakiness" ? t(reviewPrototype ? "重试恢复占比" : "不稳定性", reviewPrototype ? "Retry recovery share" : "Flakiness") : t(reviewPrototype ? "首次通过率" : "稳定性", reviewPrototype ? "First-pass rate" : "Stability")} ${t("明细", "breakdown")}`}
          className="bs-side-panel bs-breakdown-panel"
          onClose={() => setBreakdown(null)}
          opener={opener.current}
        >
          <header>
            <div>
              <h2>
                {breakdown === "flakiness"
                  ? t(
                      reviewPrototype ? "重试恢复占比" : "不稳定性",
                      reviewPrototype ? "Retry recovery share" : "Flakiness",
                    )
                  : t(
                      reviewPrototype ? "首次通过率" : "稳定性",
                      reviewPrototype ? "First-pass rate" : "Stability",
                    )}
              </h2>
              <p>
                {t(
                  "所选时间范围内的项目明细",
                  "Project breakdown for the selected date range",
                )}
              </p>
            </div>
            <button
              aria-label={t("关闭明细", "Close breakdown")}
              onClick={() => setBreakdown(null)}
            >
              <CloseOutlined />
            </button>
          </header>
          <div className="bs-breakdown-list">
            {breakdownRows.slice(0, 12).map((row) => (
              <button
                key={row.id}
                aria-label={`${t("查看执行", "Inspect execution")} ${row.id}`}
                onClick={() => {
                  setBreakdown(null);
                  setSelected(row);
                }}
              >
                <span>
                  <strong>
                    {locale === "zh" ? getTest(row).nameZh : getTest(row).name}
                  </strong>
                  <small>
                    {row.buildId} · {getBuild(row).environment}
                  </small>
                </span>
                <b>
                  {recovered(row)
                    ? t("重试恢复", "Recovered")
                    : t("通过", "Passed")}
                </b>
              </button>
            ))}
          </div>
        </AccessibleDialog>
      ) : null}

      {selected ? (
        <AccessibleDialog
          ariaLabel={t("执行证据", "Execution evidence")}
          className={`bs-side-panel${reviewPrototype ? " bs-side-panel--review" : ""}`}
          onClose={() => setSelected(null)}
          opener={opener.current}
        >
          <header>
            <div>
              <h2>
                {locale === "zh"
                  ? getTest(selected).nameZh
                  : getTest(selected).name}
              </h2>
              <p>{selected.id}</p>
            </div>
            <button
              aria-label={t("关闭证据", "Close evidence")}
              onClick={() => setSelected(null)}
            >
              <CloseOutlined />
            </button>
          </header>
          {(retriesAvailable
            ? selected.attempts
            : selected.attempts.slice(-1)
          ).map((attempt, index) => (
            <article className="bs-attempt" key={index}>
              <strong>
                {t("尝试", "Attempt")} {index + 1} · {attempt.status}
              </strong>
              <pre>
                {reviewPrototype
                  ? displayExecutionLog(attempt.log)
                  : attempt.log}
              </pre>
            </article>
          ))}
          {reviewPrototype ? (
            <FailureKnowledgeExplanation
              locale={locale}
              key={selected.id}
              execution={selected}
              format={receivedReports[selected.buildId] ?? "allure"}
            />
          ) : null}
        </AccessibleDialog>
      ) : null}
    </section>
  );
}
