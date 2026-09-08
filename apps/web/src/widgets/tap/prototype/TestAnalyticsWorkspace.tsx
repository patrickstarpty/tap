import { useMemo, useRef, useState, type KeyboardEvent } from "react";
import { AccessibleDialog } from "./AccessibleDialog";
import type { Locale } from "./model";
import {
  ANALYTICS,
  errorGroups,
  executionCsv,
  filterExecutions,
  flakyTests,
  getBuild,
  getTest,
  outcome,
  recovered,
  summarize,
  type AnalyticsScope,
  type Execution,
} from "./testAnalyticsModel";
import { TestQualityOverview, type Investigation } from "./TestQualityOverview";
import "./TestAnalyticsWorkspace.css";

type Tab = "overview" | "failure" | "flaky";

const TABS: readonly Tab[] = ["overview", "failure", "flaky"];

export function TestAnalyticsWorkspace({ locale = "zh" }: { locale?: Locale }) {
  const t = (zh: string, en: string) => (locale === "zh" ? zh : en);
  const [tab, setTab] = useState<Tab>("overview");
  const [scope, setScope] = useState<AnalyticsScope>({
    days: 14,
    plan: "TP-101",
    environment: "all",
    date: "",
    build: "",
  });
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Execution | null>(null);
  const [investigations, setInvestigations] = useState<
    Record<string, Investigation>
  >({});
  const evidenceOpener = useRef<HTMLElement | null>(null);
  const rows = useMemo(() => filterExecutions(scope), [scope]);
  const stats = summarize(rows);
  const groups = errorGroups(rows);
  const flaky = flakyTests(rows);
  const search = query.trim().toLowerCase();
  const tabRows = rows
    .filter((row) =>
      tab === "failure"
        ? row.attempts.some((attempt) => attempt.status === "failed")
        : recovered(row),
    )
    .sort(
      (a, b) =>
        Date.parse(getBuild(b).startedAt) - Date.parse(getBuild(a).startedAt),
    );
  const visible = tabRows.filter((row) =>
    [
      getTest(row).name,
      getTest(row).nameZh,
      row.id,
      row.testId,
      row.buildId,
    ].some((value) => value.toLowerCase().includes(search)),
  );
  const visibleFlaky = flaky.filter((item) =>
    [
      item.test.name,
      item.test.nameZh,
      item.test.id,
      item.key,
      ...item.rows.flatMap((row) => [row.id, row.buildId]),
    ].some((value) => value.toLowerCase().includes(search)),
  );
  const snapshotStart = scope.date
    ? scope.date
    : new Date(
        Date.parse(`${ANALYTICS.endDate}T00:00:00Z`) -
          (scope.days - 1) * 86_400_000,
      )
        .toISOString()
        .slice(0, 10);
  const snapshotEnd = scope.date || ANALYTICS.endDate;
  const tabLabels: Record<Tab, string> = {
    overview: t("概览", "Overview"),
    failure: t("失败分析", "Failure analysis"),
    flaky: t("不稳定测试", "Flaky tests"),
  };

  const selectTab = (next: Tab) => {
    setTab(next);
    setQuery("");
  };
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const index = TABS.indexOf(tab);
    const next =
      event.key === "Home"
        ? TABS[0]
        : event.key === "End"
          ? TABS.at(-1)!
          : TABS[
              (index + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) %
                TABS.length
            ];
    selectTab(next);
    document.getElementById(`ta-tab-${next}`)?.focus();
  };
  const openEvidence = (row: Execution, opener?: HTMLElement | null) => {
    evidenceOpener.current =
      opener ??
      (document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null);
    setSelected(row);
  };
  const statusLabel = (row: Execution) =>
    recovered(row)
      ? t("重试恢复", "Recovered")
      : outcome(row) === "passed"
        ? t("通过", "Passed")
        : outcome(row) === "failed"
          ? t("失败", "Failed")
          : t("跳过", "Skipped");
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

  return (
    <section className="ta-report">
      <header className="ta-report-top">
        <div>
          <div className="ta-report-kicker">TAP · QUALITY MANAGEMENT</div>
          <h1>{t("测试分析", "Test analytics")}</h1>
          <p>
            {t(
              "趋势、失败归因与不稳定测试",
              "Trends, failure attribution and flaky tests",
            )}
          </p>
        </div>
        <button
          aria-label={t("导出 CSV", "Export CSV")}
          className="ta-report-button"
          onClick={download}
        >
          {t("导出 CSV", "Export CSV")} ↗
        </button>
      </header>

      <div className="ta-scope">
        <label>
          {t("测试计划", "Test plan")}
          <select
            value={scope.plan}
            onChange={(event) =>
              setScope({ ...scope, plan: event.target.value, build: "" })
            }
          >
            <option value="all">{t("全部计划", "All plans")}</option>
            {ANALYTICS.plans.map((plan) => (
              <option key={plan.id} value={plan.id}>
                {plan.id} · {plan.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("环境", "Environment")}
          <select
            value={scope.environment}
            onChange={(event) =>
              setScope({
                ...scope,
                environment: event.target.value,
                build: "",
              })
            }
          >
            <option value="all">{t("全部环境", "All environments")}</option>
            <option value="qa">QA</option>
            <option value="staging">Staging</option>
          </select>
        </label>
        <label>
          {t("时间", "Period")}
          <select
            value={scope.days}
            onChange={(event) =>
              setScope({
                ...scope,
                days: Number(event.target.value),
                date: "",
                build: "",
              })
            }
          >
            <option value={14}>{t("最近 14 天", "Last 14 days")}</option>
            <option value={7}>{t("最近 7 天", "Last 7 days")}</option>
          </select>
        </label>
        <span className="ta-method-note">
          {t("合成演示快照", "Synthetic demo snapshot")} · {snapshotStart}{" "}
          {t("至", "to")} {snapshotEnd} · {rows.length}{" "}
          {t("次执行", "executions")}
        </span>
      </div>

      {scope.date ? (
        <div className="ta-drill">
          <button
            aria-label={t("清除日期筛选", "Clear date filter")}
            onClick={() => setScope({ ...scope, date: "", build: "" })}
          >
            <span>
              {t("日期筛选", "Date filter")} · {scope.date}
            </span>
            <span aria-hidden="true">×</span>
          </button>
          <small>
            {t(
              "当前指标仅包含该日；清除后恢复所选时间范围。",
              "Metrics are limited to this date; clear it to restore the selected period.",
            )}
          </small>
        </div>
      ) : null}

      <div
        className="ta-report-tabs"
        role="tablist"
        aria-label={t("测试分析视图", "Test analytics views")}
      >
        {TABS.map((item) => (
          <button
            aria-controls={`ta-panel-${item}`}
            aria-selected={tab === item}
            className={tab === item ? "active" : ""}
            id={`ta-tab-${item}`}
            key={item}
            onClick={() => selectTab(item)}
            onKeyDown={onTabKeyDown}
            role="tab"
            tabIndex={tab === item ? 0 : -1}
          >
            {tabLabels[item]}
            {item === "failure" ? <b>{stats.failed}</b> : null}
            {item === "flaky" ? <b>{flaky.length}</b> : null}
          </button>
        ))}
      </div>

      <div className="ta-report-metrics ta-report-metrics--four">
        <div className="ta-report-metric">
          <span>{t("执行记录", "Executions")}</span>
          <strong>{stats.total}</strong>
          <em>
            {stats.attempts} {t("次尝试", "attempts")}
          </em>
        </div>
        <div className="ta-report-metric">
          <span>{t("最终通过率", "Final pass rate")}</span>
          <strong>
            {stats.finalRate === null ? "—" : `${Math.round(stats.finalRate)}%`}
          </strong>
          <em>
            {stats.passed} {t("次最终通过", "final passed")}
          </em>
        </div>
        <div className="ta-report-metric">
          <span>{t("最终失败", "Final failures")}</span>
          <strong className="ta-red">{stats.failed}</strong>
          <em>
            {stats.failedAttempts} {t("次失败尝试", "failed attempts")}
          </em>
        </div>
        <div className="ta-report-metric">
          <span>{t("重试恢复", "Recovered")}</span>
          <strong className="ta-purple">{stats.recovered}</strong>
          <em>
            {stats.retryMs} ms {t("重试耗时", "retry time")}
          </em>
        </div>
      </div>

      {tab === "overview" ? (
        <div
          aria-labelledby="ta-tab-overview"
          id="ta-panel-overview"
          role="tabpanel"
        >
          {rows.length ? (
            <TestQualityOverview
              rows={rows}
              planId={scope.plan}
              locale={locale}
              investigations={investigations}
              onInvestigate={(id) =>
                setInvestigations((current) => ({
                  ...current,
                  [id]: "investigating",
                }))
              }
              onFlaky={() => selectTab("flaky")}
              onPlan={(plan) => setScope({ ...scope, plan })}
              onDate={(date) => setScope({ ...scope, date, build: "" })}
              onEvidence={openEvidence}
            />
          ) : (
            <p role="status">
              {t("当前范围没有执行证据", "No execution evidence in this scope")}
            </p>
          )}
        </div>
      ) : (
        <section
          aria-labelledby={`ta-tab-${tab}`}
          className="ta-report-section"
          id={`ta-panel-${tab}`}
          role="tabpanel"
        >
          <div className="ta-analysis-toolbar">
            <div>
              <h2>{tabLabels[tab]}</h2>
              <p>
                {tab === "failure"
                  ? t(
                      "按错误特征归组，辅助排查；归因尚待确认。",
                      "Grouped by error signature for investigation; causes remain unconfirmed.",
                    )
                  : t(
                      "首次失败、重试后通过的测试变体。",
                      "Test variants that failed first and passed after retry.",
                    )}
              </p>
            </div>
            <input
              aria-label={t(
                "搜索测试或执行 ID",
                "Search tests or execution IDs",
              )}
              placeholder={t(
                "搜索测试或执行 ID",
                "Search tests or execution IDs",
              )}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>

          {tab === "failure" ? (
            <div className="ta-error-list">
              {groups.map((group) => (
                <article className="ta-error-detail" key={group.id}>
                  <div>
                    <h3>{locale === "zh" ? group.nameZh : group.name}</h3>
                    <p>
                      {locale === "zh" ? group.hypothesisZh : group.hypothesis}
                    </p>
                    <code>{group.message}</code>
                  </div>
                  <strong>
                    {group.failedAttempts}
                    <small> {t("次尝试", "attempts")}</small>
                  </strong>
                </article>
              ))}
            </div>
          ) : (
            <div className="ta-report-table">
              <table>
                <thead>
                  <tr>
                    <th>{t("测试", "Test")}</th>
                    <th>{t("环境", "Environment")}</th>
                    <th>{t("恢复次数", "Recoveries")}</th>
                    <th>{t("重试耗时", "Retry time")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleFlaky.map((item) => (
                    <tr key={item.key}>
                      <td>
                        <button
                          className="ta-report-link"
                          onClick={(event) =>
                            openEvidence(item.rows.at(-1)!, event.currentTarget)
                          }
                        >
                          {locale === "zh" ? item.test.nameZh : item.test.name}
                        </button>
                      </td>
                      <td>{item.environment}</td>
                      <td>{item.stats.recovered}</td>
                      <td>{item.stats.retryMs} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="ta-report-table">
            <h3>{t("执行证据", "Execution evidence")}</h3>
            {visible.length ? (
              <table>
                <thead>
                  <tr>
                    <th>{t("执行", "Execution")}</th>
                    <th>{t("测试", "Test")}</th>
                    <th>{t("构建", "Build")}</th>
                    <th>{t("状态", "Status")}</th>
                    <th>{t("尝试次数", "Attempts")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.slice(0, 12).map((row) => (
                    <tr key={row.id}>
                      <td>
                        <button
                          className="ta-report-link"
                          onClick={(event) =>
                            openEvidence(row, event.currentTarget)
                          }
                        >
                          {row.id}
                        </button>
                      </td>
                      <td>
                        {locale === "zh"
                          ? getTest(row).nameZh
                          : getTest(row).name}
                      </td>
                      <td>{row.buildId}</td>
                      <td>
                        <span
                          className={`ta-result ta-result--${outcome(row)}`}
                        >
                          {statusLabel(row)}
                        </span>
                      </td>
                      <td>{row.attempts.length || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p role="status">
                {t(
                  "当前范围没有执行证据",
                  "No execution evidence in this scope",
                )}
              </p>
            )}
          </div>
        </section>
      )}

      {selected ? (
        <AccessibleDialog
          ariaLabel={t("执行证据", "Execution evidence")}
          className="ta-evidence-dialog"
          onClose={() => setSelected(null)}
          opener={evidenceOpener.current}
        >
          <button
            onClick={() => setSelected(null)}
            aria-label={t("关闭证据", "Close evidence")}
          >
            ×
          </button>
          <div className="ta-detail-heading">
            <div>
              <h2>
                {locale === "zh"
                  ? getTest(selected).nameZh
                  : getTest(selected).name}
              </h2>
              <p>
                {selected.id} · {getBuild(selected).environment}
              </p>
            </div>
          </div>
          {selected.attempts.length ? (
            selected.attempts.map((attempt, index) => (
              <article className="ta-attempt" key={index}>
                <strong>
                  {t("尝试", "Attempt")} {index + 1} ·{" "}
                  {attempt.status === "passed"
                    ? t("通过", "Passed")
                    : t("失败", "Failed")}
                </strong>
                <pre>{attempt.log}</pre>
              </article>
            ))
          ) : (
            <p>
              {t(
                "此执行被跳过，没有尝试或执行日志。",
                "This execution was skipped and has no attempts or logs.",
              )}
            </p>
          )}
        </AccessibleDialog>
      ) : null}
    </section>
  );
}
