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

type Breakdown = "stability" | "flakiness" | null;

const RANGE_OPTIONS = [
  { label: "1D", days: 1 },
  { label: "7D", days: 7 },
  { label: "30D", days: 30 },
  { label: "6M", days: 180 },
  { label: "1Y", days: 365 },
  { label: "2Y", days: 730 },
] as const;

function ChartFrame({ children }: { children: ReactNode }) {
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
          24 Aug
        </text>
        <text x="244" y="204">
          31 Aug
        </text>
        <text x="448" y="204">
          6 Sep
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
}: {
  locale?: Locale;
  initialPlanId?: string;
}) {
  const t = (zh: string, en: string) => (locale === "zh" ? zh : en);
  const [scope, setScope] = useState<AnalyticsScope>({
    days: 30,
    plan: initialPlanId ?? "all",
    environment: "all",
    date: "",
    build: "",
  });
  const [draftScope, setDraftScope] = useState(scope);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [catalogOpen, setCatalogOpen] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [breakdown, setBreakdown] = useState<Breakdown>(null);
  const [selected, setSelected] = useState<Execution | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  const rows = useMemo(() => filterExecutions(scope), [scope]);
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
    const url = URL.createObjectURL(
      new Blob([executionCsv(rows)], { type: "text/csv;charset=utf-8" }),
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
    <section className="bs-dashboard">
      <header className="bs-page-header">
        <div>
          <nav aria-label={t("面包屑", "Breadcrumb")}>
            <span>{t("仪表盘", "Dashboards")}</span>
            <b>/</b>
            <span>{t("演示仪表盘", "Demo Dashboard")}</span>
          </nav>
          <h1>{t("演示仪表盘", "Demo Dashboard")}</h1>
        </div>
        <div className="bs-page-actions">
          <button
            aria-label={t("下载仪表盘", "Download dashboard")}
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

      <section
        aria-label={t("仪表盘组件", "Dashboard widgets")}
        className="bs-widget-grid"
      >
        <Widget title={t("摘要", "Summary")} className="bs-summary-widget">
          <div className="bs-summary-row">
            <div>
              <span>{t("测试摘要", "Tests Summary")}</span>
              <strong data-testid="tests-summary-value">{stats.total}</strong>
              <i className="bs-summary-bar bs-summary-bar--tests" />
            </div>
            <div>
              <span>{t("失败率", "Failure Rate")}</span>
              <strong>{failureRate.toFixed(2)}%</strong>
            </div>
            <div>
              <span>{t("失败数量", "Failure Count")}</span>
              <strong>{stats.failed}</strong>
              <i className="bs-summary-bar bs-summary-bar--failures" />
            </div>
            <div>
              <span>{t("稳定性", "Stability")}</span>
              <strong>{stability.toFixed(2)}%</strong>
            </div>
            <div>
              <span>{t("不稳定性", "Flakiness")}</span>
              <strong>{flakiness.toFixed(2)}%</strong>
              <small>
                {stats.recovered} / {stats.attempted}{" "}
                {t("次测试执行存在不稳定", "test executions are flaky")}
              </small>
            </div>
            <div>
              <span>{t("覆盖平台", "Platforms Covered")}</span>
              <strong>6</strong>
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
                    style={{ width: `${passed * 26}px` }}
                  />
                  <b className="is-red" style={{ width: `${failed * 26}px` }} />
                  <b
                    className="is-grey"
                    style={{ width: `${skipped * 26}px` }}
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
          title={t("稳定性", "Stability")}
          className="bs-stability-widget"
        >
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
                points={points(stabilityValues.map((v) => Math.max(0, v - 7)))}
              />
              <polyline
                className="bs-line is-orange"
                points={points(stabilityValues.map((v) => Math.max(0, v - 19)))}
              />
            </ChartFrame>
          </button>
        </Widget>

        <Widget
          title={t("不稳定性", "Flakiness")}
          className="bs-flakiness-widget"
        >
          <div className="bs-chart-summary">
            <span>{t("平均不稳定性", "Average Flakiness")}</span>
            <strong>{flakiness.toFixed(2)}%</strong>
            <small>
              {stats.recovered}/{stats.attempted}{" "}
              {t("次测试执行存在不稳定", "test executions are flaky")}
            </small>
          </div>
          <div className="bs-legend">
            <span>
              <i className="is-blue" />
              Flakiness
            </span>
          </div>
          <button
            aria-label={t("查看不稳定性明细", "View Flakiness breakdown")}
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
        </Widget>

        <Widget
          title={t("浏览器摘要", "Browser wise summary")}
          className="bs-browser-widget"
        >
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
                  {t("稳定性", "Stability")}
                  <b>{rate}</b>
                </span>
              </div>
            ))}
          </div>
        </Widget>

        <Widget
          title={t("构建性能", "Build Performance")}
          className="bs-performance-widget"
        >
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
        </Widget>
      </section>

      {filtersOpen ? (
        <AccessibleDialog
          ariaLabel={t("仪表盘筛选", "Dashboard filters")}
          className="bs-side-panel"
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
              "Stability",
              "Flakiness",
              "Component Summary",
              "Build Performance",
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
              "拥有链接的团队成员可以查看此演示仪表盘。",
              "Team members with the link can view this demo dashboard.",
            )}
          </p>
          <button className="bs-add-button" onClick={() => setShareOpen(false)}>
            {t("复制链接", "Copy link")}
          </button>
        </AccessibleDialog>
      ) : null}

      {breakdown ? (
        <AccessibleDialog
          ariaLabel={`${breakdown === "flakiness" ? t("不稳定性", "Flakiness") : t("稳定性", "Stability")} ${t("明细", "breakdown")}`}
          className="bs-side-panel bs-breakdown-panel"
          onClose={() => setBreakdown(null)}
          opener={opener.current}
        >
          <header>
            <div>
              <h2>
                {breakdown === "flakiness"
                  ? t("不稳定性", "Flakiness")
                  : t("稳定性", "Stability")}
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
          className="bs-side-panel"
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
          {selected.attempts.map((attempt, index) => (
            <article className="bs-attempt" key={index}>
              <strong>
                {t("尝试", "Attempt")} {index + 1} · {attempt.status}
              </strong>
              <pre>{attempt.log}</pre>
            </article>
          ))}
        </AccessibleDialog>
      ) : null}
    </section>
  );
}
