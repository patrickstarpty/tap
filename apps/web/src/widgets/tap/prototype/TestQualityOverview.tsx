import { TestQualitySignals } from "./TestQualitySignals";
import { ArrowRightOutlined } from "@ant-design/icons";
import type { Locale } from "./model";
import {
  errorGroups,
  flakyTests,
  outcome,
  qualitySummary,
  summarize,
  type Execution,
} from "./testAnalyticsModel";

export type Investigation = "unreviewed" | "investigating";
export function TestQualityOverview({
  rows,
  planId,
  locale,
  investigations,
  onInvestigate,
  onFlaky,
  onPlan,
  onDate,
  onEvidence,
}: {
  rows: readonly Execution[];
  planId: string;
  locale: Locale;
  investigations: Readonly<Record<string, Investigation>>;
  onInvestigate: (id: string) => void;
  onFlaky: () => void;
  onPlan: (id: string) => void;
  onDate: (date: string) => void;
  onEvidence: (row: Execution) => void;
}) {
  const t = (cn: string, en: string) => (locale === "zh" ? cn : en);
  const stats = summarize(rows);
  const assessment = qualitySummary(rows, planId);
  const groups = errorGroups(rows)
    .map((group) => ({
      ...group,
      finalFailures: group.rows.filter((row) => outcome(row) === "failed")
        .length,
      latestFailures: group.rows.filter(
        (row) =>
          assessment.latest.some((latest) => latest.id === row.id) &&
          outcome(row) === "failed",
      ).length,
    }))
    .sort(
      (a, b) =>
        b.latestFailures - a.latestFailures ||
        b.finalFailures - a.finalFailures ||
        b.tests - a.tests,
    );
  const pending = groups.filter(
    (group) => investigations[group.id] !== "investigating",
  ).length;
  const flaky = flakyTests(rows);
  return (
    <div className="ta-quality-overview">
      <div className="ta-quality-metrics">
        <div>
          <span>{t("最近结果仍失败", "Latest results still failing")}</span>
          <strong className={assessment.latestFailed ? "ta-red" : "ta-teal"}>
            {assessment.latestFailed}
            <small> / {assessment.latest.length}</small>
          </strong>
          <small>
            {t(
              "每个场景 × 环境取最近一次",
              "Latest execution per scenario × environment",
            )}
          </small>
        </div>
        <div>
          <span>{t("累计最终失败", "Final failures in period")}</span>
          <strong>
            {stats.failed}
            <small> / {stats.total}</small>
          </strong>
          <small>
            {t("当前筛选范围内的执行", "Executions in the selected scope")}
          </small>
        </div>
        <div>
          <span>{t("待排查错误组", "Error groups to investigate")}</span>
          <strong>
            {pending}
            <small> / {groups.length}</small>
          </strong>
          <small>
            {groups.length - pending}{" "}
            {t("组排查中 · 本页状态", "investigating · page-local status")}
          </small>
        </div>
        <button onClick={onFlaky}>
          <span>{t("疑似不稳定测试", "Suspected flaky variants")}</span>
          <strong className="ta-purple">
            {flaky.length}
            <ArrowRightOutlined />
          </strong>
          <small>
            {stats.recovered}{" "}
            {t("次执行经重试恢复", "executions recovered on retry")}
          </small>
        </button>
      </div>
      <TestQualitySignals
        rows={rows}
        planId={planId}
        locale={locale}
        onDate={onDate}
        onEvidence={onEvidence}
      />
      <div className="ta-quality-columns">
        <section className="ta-action-queue" aria-labelledby="ta-action-title">
          <header>
            <div>
              <h3 id="ta-action-title">
                {t("优先处理", "Prioritize investigation")}
              </h3>
              <p>
                {t(
                  "最近仍失败 → 历史失败 → 重试恢复；不是业务严重级别。",
                  "Latest failures first, then historical failures and recoveries; not business severity.",
                )}
              </p>
            </div>
            <span>
              {groups.length} {t("个错误组", "error groups")}
            </span>
          </header>
          {groups.length ? (
            groups.map((group) => (
              <article className="ta-action-row" key={group.id}>
                <div className="ta-action-description">
                  <div>
                    <code>{group.id}</code>
                    <span
                      className={
                        group.latestFailures
                          ? "ta-action-flag"
                          : "ta-action-muted"
                      }
                    >
                      {group.latestFailures
                        ? t("最近仍失败", "Still failing")
                        : group.finalFailures
                          ? t("历史失败待核验", "Review historical failures")
                          : t("重试恢复", "Recovered on retry")}
                    </span>
                  </div>
                  <h4>{locale === "zh" ? group.nameZh : group.name}</h4>
                  <div
                    className="ta-action-impact"
                    role="img"
                    aria-label={`${group.finalFailures} ${t("次最终失败", "final failures")} · ${group.recovered} ${t("次重试恢复", "recoveries")}`}
                  >
                    <span
                      style={{
                        width: `${(group.finalFailures / Math.max(...groups.map((g) => g.rows.length))) * 100}%`,
                        background: "#c64f5a",
                      }}
                    />
                    <span
                      style={{
                        width: `${(group.recovered / Math.max(...groups.map((g) => g.rows.length))) * 100}%`,
                        background: "#8865bf",
                      }}
                    />
                  </div>
                  <p>
                    {group.tests} {t("个测试场景", "test scenarios")} ·{" "}
                    {group.builds} {t("个构建", "builds")} ·{" "}
                    {group.finalFailures} {t("次最终失败", "final failures")} ·{" "}
                    {group.recovered} {t("次重试恢复", "recoveries")}
                  </p>
                  <small>
                    {t("建议核验", "Investigate")}：
                    {locale === "zh" ? group.categoryZh : group.category} ·{" "}
                    {t("归因待确认", "cause unconfirmed")}
                  </small>
                </div>
                <button
                  className="ta-report-button"
                  aria-label={`${investigations[group.id] === "investigating" ? t("继续排查", "Continue investigation") : t("开始排查", "Start investigation")} ${group.id}`}
                  onClick={() => onInvestigate(group.id)}
                >
                  {investigations[group.id] === "investigating"
                    ? t("继续排查", "Continue")
                    : t("开始排查", "Investigate")}
                  <ArrowRightOutlined />
                </button>
              </article>
            ))
          ) : (
            <div className="ta-queue-empty">
              <strong>
                {t("当前范围没有失败尝试", "No failed attempts in this scope")}
              </strong>
              <p>
                {t(
                  "仍需核验执行覆盖与发布依据。",
                  "Execution coverage and release evidence still need review.",
                )}
              </p>
            </div>
          )}
        </section>
        <aside className="ta-quality-readiness">
          <header>
            <h3>{t("发布评审依据", "Release review evidence")}</h3>
            <span>{t("待评审", "Review needed")}</span>
          </header>
          <div className="ta-coverage-heading">
            <strong>{t("场景执行覆盖", "Scenario execution coverage")}</strong>
            <span>
              {assessment.executedScenarios} / {assessment.plannedScenarios}
            </span>
          </div>
          <div
            className="ta-coverage-track"
            role="img"
            aria-label={`${t("已执行场景", "Executed scenarios")} ${assessment.executedScenarios} / ${assessment.plannedScenarios}`}
          >
            <span
              style={{
                width: `${assessment.plannedScenarios ? (assessment.executedScenarios / assessment.plannedScenarios) * 100 : 0}%`,
              }}
            />
          </div>
          <small>
            {t(
              "范围内有实际尝试的场景 / 所选计划全部场景；不等于需求覆盖率。",
              "Scenarios with attempts in scope / all scenarios in selected plans; not requirement coverage.",
            )}
          </small>
          {assessment.missingPlans.map((plan) => (
            <button
              key={plan.id}
              className="ta-coverage-gap"
              onClick={() => onPlan(plan.id)}
            >
              <span>
                {plan.id} · {t("存在未执行场景", "Has unexecuted scenarios")}
              </span>
              <ArrowRightOutlined />
            </button>
          ))}
          <details className="ta-readiness-details">
            <summary>
              {t("评审依据与缺失输入", "Review evidence & missing inputs")}
            </summary>
            <p className="ta-readiness-lead">
              {assessment.latestFailed
                ? t(
                    `最近结果中仍有 ${assessment.latestFailed} 次失败。`,
                    `${assessment.latestFailed} latest result(s) still fail.`,
                  )
                : t(
                    "当前范围的最近结果未见失败。",
                    "No failures in the latest results within this scope.",
                  )}
            </p>
            <p>
              {t(
                "需求覆盖与缺陷状态尚未接入，不能据此批准发布。",
                "Requirement coverage and defect status are not connected; this cannot authorize a release.",
              )}
            </p>
            <dl>
              <div>
                <dt>{t("关键场景标记", "Critical scenario tags")}</dt>
                <dd>{t("未提供", "Not supplied")}</dd>
              </div>
              <div>
                <dt>{t("需求关联", "Requirement links")}</dt>
                <dd>{t("未接入", "Not connected")}</dd>
              </div>
              <div>
                <dt>{t("缺陷处理状态", "Defect status")}</dt>
                <dd>{t("未接入", "Not connected")}</dd>
              </div>
            </dl>
          </details>
        </aside>
      </div>
    </div>
  );
}
