import { useState } from "react";
import type { Locale } from "./model";
import { TestAnalyticsChart } from "./TestAnalyticsChart";
import {
  dailyRows,
  getBuild,
  getTest,
  outcome,
  qualitySummary,
  recovered,
  summarize,
  type Execution,
} from "./testAnalyticsModel";

export function TestQualitySignals({
  rows,
  planId,
  locale,
  onDate,
  onEvidence,
}: {
  rows: readonly Execution[];
  planId: string;
  locale: Locale;
  onDate: (date: string) => void;
  onEvidence: (row: Execution) => void;
}) {
  const t = (cn: string, en: string) => (locale === "zh" ? cn : en);
  const [hover, setHover] = useState<string | null>(null);
  const daily = dailyRows(rows);
  const stats = summarize(rows);
  const latest = qualitySummary(rows, planId).latest;
  const tests = [...new Set(latest.map((row) => row.testId))];
  const environments = [
    ...new Set(latest.map((row) => getBuild(row).environment)),
  ];
  const directPass = rows.filter(
    (row) => outcome(row) === "passed" && !recovered(row),
  ).length;
  const parts = [
    {
      label: t("直接通过", "Passed without recovery"),
      count: directPass,
      color: "#489882",
    },
    {
      label: t("重试恢复", "Recovered on retry"),
      count: stats.recovered,
      color: "#8865bf",
    },
    {
      label: t("最终失败", "Finally failed"),
      count: stats.failed,
      color: "#c64f5a",
    },
    { label: t("跳过", "Skipped"), count: stats.skipped, color: "#a9b0b8" },
  ];
  const name = (row: Execution) =>
    locale === "zh" ? getTest(row).nameZh : getTest(row).name;
  const stateLabel = (row: Execution) =>
    recovered(row)
      ? t("重试恢复", "Recovered")
      : outcome(row) === "passed"
        ? t("通过", "Passed")
        : outcome(row) === "failed"
          ? t("失败", "Failed")
          : t("跳过", "Skipped");
  return (
    <section
      className="ta-quality-signals"
      aria-label={t("质量可视化", "Quality visualizations")}
    >
      <div className="ta-signals-pair">
        <div className="ta-signal-trend">
          <TestAnalyticsChart
            minPlotWidth={260}
            title={t("质量趋势", "Quality trend")}
            note={t("点击异常日期查看失败", "Select a date to investigate")}
            dates={daily.map((day) => day.date)}
            hover={hover}
            onHover={setHover}
            max={100}
            unit="%"
            series={[
              {
                name: t("首次通过率", "First-attempt pass rate"),
                color: "#8865bf",
                values: daily.map((day) => day.stats.firstRate),
              },
              {
                name: t("最终通过率", "Final pass rate"),
                color: "#489882",
                values: daily.map((day) => day.stats.finalRate),
              },
            ]}
            onSelect={onDate}
            selectLabel={(date) =>
              t(`分析质量趋势 ${date}`, `Investigate quality trend ${date}`)
            }
          />
        </div>
        <section className="ta-signal-matrix">
          <header>
            <h3>{t("场景 × 环境", "Scenario × environment")}</h3>
            <span>
              {t(
                "最近一次结果 · 点击查看",
                "Latest result · select to inspect",
              )}
            </span>
          </header>
          <div className="ta-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{t("场景", "Scenario")}</th>
                  {environments.map((environment) => (
                    <th key={environment}>
                      {environment === "qa" ? "QA" : "Staging"}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tests.map((id) => {
                  const example = latest.find((row) => row.testId === id)!;
                  return (
                    <tr key={id}>
                      <th scope="row" title={name(example)}>
                        {name(example)}
                      </th>
                      {environments.map((environment) => {
                        const row = latest.find(
                          (r) =>
                            r.testId === id &&
                            getBuild(r).environment === environment,
                        );
                        return (
                          <td key={environment}>
                            {row ? (
                              <button
                                className={`ta-signal-cell ta-cell--${recovered(row) ? "recovered" : outcome(row)}`}
                                onClick={() => onEvidence(row)}
                                aria-label={`${t("查看最近执行", "Inspect latest execution")} ${row.id} · ${stateLabel(row)}`}
                                title={`${name(row)} · ${getBuild(row).date} · ${row.buildId}`}
                              >
                                <span>{stateLabel(row)}</span>
                                <small>{getBuild(row).date.slice(5)}</small>
                              </button>
                            ) : (
                              <span className="ta-signal-no-run">
                                {t("未执行", "Not run")}
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
      <section className="ta-outcome-distribution">
        <header>
          <h3>{t("执行结果分布", "Execution outcome mix")}</h3>
          <span>
            {stats.total}{" "}
            {t("次执行 · 当前范围累计", "executions · selected period")}
          </span>
        </header>
        <div
          className="ta-outcome-strip"
          role="img"
          aria-label={t(
            `执行结果分布：通过 ${directPass}，重试恢复 ${stats.recovered}，失败 ${stats.failed}，跳过 ${stats.skipped}`,
            `Execution mix: passed ${directPass}, recovered ${stats.recovered}, failed ${stats.failed}, skipped ${stats.skipped}`,
          )}
        >
          {parts
            .filter((part) => part.count)
            .map((part) => (
              <span
                key={part.label}
                style={{
                  width: `${(part.count / stats.total) * 100}%`,
                  background: part.color,
                }}
                title={`${part.label}: ${part.count} (${((part.count / stats.total) * 100).toFixed(1)}%)`}
              />
            ))}
        </div>
        <div className="ta-outcome-legend">
          {parts.map((part) => (
            <span key={part.label}>
              <i style={{ background: part.color }} />
              {part.label}
              <strong>{part.count}</strong>
              <small>
                {stats.total
                  ? ((part.count / stats.total) * 100).toFixed(1)
                  : "0.0"}
                %
              </small>
            </span>
          ))}
        </div>
      </section>
    </section>
  );
}
