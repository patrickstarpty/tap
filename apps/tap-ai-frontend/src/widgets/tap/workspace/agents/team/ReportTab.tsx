import { Button } from "antd";
import { useState, type ReactNode } from "react";

import {
  agentTeamExportUrl,
  resolvePeriod,
  useTeamReport,
  type AgentTeamMemberRow,
  type AgentTeamReportView,
  type TeamPeriod,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { bucketAxis, fillBuckets, type Bucket } from "./buckets";
import { TrendChart, type TrendSeries } from "./charts";
import {
  EMPTY,
  formatCost,
  formatCount,
  formatDateTime,
  formatDuration,
  teamLocale,
} from "./format";
import { LoadError, TeamSkeleton } from "./layout";
import { RunValue, type CellContext } from "./memberCells";
import { PeopleTable } from "./PeopleTable";
import {
  PeriodPicker,
  useValidPeriod,
  type PeriodSelection,
} from "./PeriodPicker";
import { RosterTable } from "./RosterTable";
import { RuntimeBadge, RuntimeMark } from "./RuntimeBadge";

export interface ReportTabProps {
  projectId: string;
  copy: WorkspaceCopy;
}

const NOOP = () => undefined;

function lastMonthSelection(): PeriodSelection {
  return {
    preset: "lastMonth",
    custom: resolvePeriod("lastMonth", new Date()),
  };
}

/** Per-bucket sum of every member's trend points. */
export function teamTrend(
  members: AgentTeamMemberRow[],
  labels: { runs: string; failures: string },
  axis: Bucket[],
): TrendSeries[] {
  const runs = new Map<string, number>();
  const failures = new Map<string, number>();
  for (const member of members)
    for (const point of member.trend) {
      runs.set(
        point.bucketStart,
        (runs.get(point.bucketStart) ?? 0) + point.runs,
      );
      failures.set(
        point.bucketStart,
        (failures.get(point.bucketStart) ?? 0) + point.failures,
      );
    }
  if (axis.length === 0 || runs.size === 0) return [];
  const series = (label: string, source: Map<string, number>) => ({
    label,
    points: fillBuckets(
      axis,
      [...source].map(([bucketStart, value]) => ({ bucketStart, value })),
    ),
  });
  return [series(labels.runs, runs), series(labels.failures, failures)];
}

interface Metric {
  label: string;
  current: number | null;
  previous: number | null;
  format: (value: number) => string;
  /** False for point-in-time counts (roster size, idle) that are not
   * period-comparable: only the current value is shown. */
  comparable?: false;
  /** Derived from run data: marked by the runtime status. */
  runtime?: true;
}

/** Downloads the CSV zip as a blob so failures can be reported inline. */
async function downloadExport(projectId: string, period: TeamPeriod) {
  const response = await fetch(agentTeamExportUrl(projectId, period));
  if (!response.ok) throw new Error(`export failed: ${response.status}`);
  const url = URL.createObjectURL(await response.blob());
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = `agent-team-${period.from}-${period.to}.zip`;
    link.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Direction of a comparable change, for the soft cell tint. */
function changeTrend(metric: Metric): "up" | "down" | "flat" | "none" {
  const { current, previous } = metric;
  if (metric.comparable === false) return "none";
  if (current === null || previous === null || previous === 0) return "none";
  return current > previous ? "up" : current < previous ? "down" : "flat";
}

function ChangeCell({ metric }: { metric: Metric }) {
  const { current, previous, format } = metric;
  if (metric.comparable === false) return <>{EMPTY}</>;
  if (current === null || previous === null || previous === 0)
    return <>{EMPTY}</>;
  const delta = current - previous;
  const percent = Math.round((delta / previous) * 100);
  const arrow = delta > 0 ? "▲" : delta < 0 ? "▼" : "▬";
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
  // The percent's sign follows the rounded value so it never reads "−0%".
  const percentSign = percent > 0 ? "+" : percent < 0 ? "−" : "";
  return (
    <span className="tap-team-change-chip">
      {arrow} {sign}
      {format(Math.abs(delta))} ({percentSign}
      {Math.abs(percent)}%)
    </span>
  );
}

export function ReportTab({ projectId, copy }: ReportTabProps) {
  const text = copy.agentTeam;
  const labels = text.report;
  const locale = teamLocale(copy);
  // Independent of the other tabs: the report defaults to last month.
  const [selection, setSelection] =
    useState<PeriodSelection>(lastMonthSelection);
  const { period, invalid: rangeInvalid } = useValidPeriod(selection);
  const report = useTeamReport(projectId, period, selection.preset);
  const view: AgentTeamReportView | undefined = report.data;
  // keepPreviousData shows the last report while a new period loads; say so
  // and export only what is on screen.
  const refreshing = report.isPlaceholderData === true;
  const [exporting, setExporting] = useState(false);
  const [exportFailed, setExportFailed] = useState(false);
  const exportCsv = async (target: TeamPeriod) => {
    setExporting(true);
    setExportFailed(false);
    try {
      await downloadExport(projectId, target);
    } catch {
      setExportFailed(true);
    } finally {
      setExporting(false);
    }
  };

  const picker = (
    <div className="tap-team-report-controls">
      <PeriodPicker copy={copy} value={selection} onChange={setSelection} />
      <div className="tap-team-report-actions">
        <Button onClick={() => window.print()}>
          <PrintIcon />
          {text.exportPdf}
        </Button>
        <Button
          type="primary"
          loading={exporting}
          disabled={view === undefined || rangeInvalid || refreshing}
          onClick={() => {
            if (view !== undefined) void exportCsv(view.period);
          }}
        >
          {text.exportCsv}
        </Button>
      </div>
      {exportFailed ? (
        <p role="alert" className="tap-team-inline-error">
          {text.exportFailed}
        </p>
      ) : null}
      {refreshing ? (
        <p role="status" className="tap-team-refreshing">
          {text.loading}
        </p>
      ) : null}
    </div>
  );

  if (view === undefined) {
    return (
      <div className="tap-team-report">
        {picker}
        {rangeInvalid ? null : report.isError ? (
          <LoadError copy={copy} onRetry={() => void report.refetch()} />
        ) : (
          <TeamSkeleton copy={copy} kpis={false} cards={2} />
        )}
      </div>
    );
  }

  const { current, previous } = view.data;
  const ctx: CellContext = {
    copy,
    locale,
    timezone: view.timezone,
    runtimeStatus: view.runtimeStatus,
  };
  const count = (value: number) => formatCount(value, locale);
  const cost = (value: number) => formatCost(String(value), locale);
  const num = (value: string | null) => (value === null ? null : Number(value));
  const cs = current.summary;
  const ps = previous.summary;
  const metrics: Metric[] = [
    {
      label: text.metrics.agentTotal,
      current: cs.agentTotal,
      previous: ps.agentTotal,
      format: count,
      comparable: false,
    },
    {
      label: text.metrics.active,
      current: cs.active,
      previous: ps.active,
      format: count,
      runtime: true,
    },
    {
      label: text.metrics.idle,
      current: cs.idle,
      previous: ps.idle,
      format: count,
      comparable: false,
      runtime: true,
    },
    {
      label: text.metrics.runs,
      current: cs.runs,
      previous: ps.runs,
      format: count,
      runtime: true,
    },
    {
      label: text.metrics.latency,
      current: cs.latencyTotalMs,
      previous: ps.latencyTotalMs,
      format: (value) => formatDuration(value, locale, text.durationUnits),
      runtime: true,
    },
    {
      label: text.metrics.cost,
      current: num(cs.costUsd),
      previous: num(ps.costUsd),
      format: cost,
      runtime: true,
    },
    {
      label: text.metrics.maintenance,
      current: cs.maintenanceTotal,
      previous: ps.maintenanceTotal,
      format: count,
    },
    {
      label: text.metrics.servedAvg,
      // Its denominator is the period's callers, which is run data; the
      // contract may still type it as a number.
      current: cs.servedAvg as number | null,
      previous: ps.servedAvg as number | null,
      format: count,
      runtime: true,
    },
    {
      label: text.metrics.ownersAvg,
      current: cs.ownersAvg,
      previous: ps.ownersAvg,
      format: count,
    },
  ];
  const show = (
    metric: Metric,
    value: number | null,
    column: "current" | "previous",
  ): ReactNode => {
    const formatted =
      value === null || Number.isNaN(value) ? null : metric.format(value);
    if (metric.runtime !== true) return formatted ?? EMPTY;
    // Sync lag only affects recent runs: annotate the current period only.
    if (column === "previous" && view.runtimeStatus === "syncing")
      return formatted ?? EMPTY;
    return <RunValue value={formatted} ctx={ctx} />;
  };
  const comparedWith = labels.comparedWith
    .replace("{from}", view.data.previousPeriod.from)
    .replace("{to}", view.data.previousPeriod.to);
  const trend = teamTrend(
    current.members,
    { runs: text.metrics.runs, failures: labels.failures },
    bucketAxis(view.period, view.granularity),
  );

  return (
    <div className="tap-team-report">
      {picker}
      <article className="tap-team-report-sheet">
        <header className="tap-team-report-header">
          <h2>{labels.title}</h2>
          <div className="tap-team-report-meta">
            <p>
              {labels.project}: {projectId}
            </p>
            <p>
              {labels.period}: {view.period.from} – {view.period.to} (
              {view.timezone})
            </p>
            <p>{comparedWith}</p>
            <p>
              {text.updatedAt
                .replace(
                  "{time}",
                  formatDateTime(view.computedAt, view.timezone, locale),
                )
                .replace("{timezone}", view.timezone)}
            </p>
          </div>
          {view.runtimeStatus === "ok" ? null : (
            <RuntimeBadge
              status={view.runtimeStatus}
              computedAt={view.computedAt}
              timezone={view.timezone}
              copy={copy}
            />
          )}
        </header>
        <div className="tap-team-report-grid">
          <section className="tap-team-report-section">
            <h3>
              {labels.summaryHeading} · {comparedWith}
            </h3>
            <table className="tap-team-report-table">
              <caption className="tap-team-sr-only">
                {labels.comparisonHeading}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{labels.metric}</th>
                  <th scope="col">{labels.current}</th>
                  <th scope="col">{labels.previous}</th>
                  <th scope="col">{labels.change}</th>
                </tr>
              </thead>
              <tbody>
                {metrics.map((metric) => (
                  <tr key={metric.label}>
                    <th scope="row">{metric.label}</th>
                    <td>{show(metric, metric.current, "current")}</td>
                    <td>
                      {metric.comparable === false
                        ? EMPTY
                        : show(metric, metric.previous, "previous")}
                    </td>
                    <td
                      className="tap-team-change"
                      data-trend={changeTrend(metric)}
                    >
                      <ChangeCell metric={metric} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="tap-team-report-section">
            <h3>{labels.trendHeading}</h3>
            {view.runtimeStatus === "unavailable" ? (
              <p>
                {EMPTY}
                <RuntimeMark status="unavailable" copy={copy} />
              </p>
            ) : trend.length === 0 ? (
              <p className="tap-team-empty tap-team-empty--quiet">
                {labels.noTrend}
              </p>
            ) : (
              <TrendChart
                series={trend}
                unit={text.profile.runsUnit}
                locale={locale}
                hint={text.team.chartHint}
              />
            )}
          </section>
        </div>
        <section className="tap-team-report-section">
          <h3>{text.team.rosterHeading}</h3>
          <RosterTable
            rows={current.members}
            ctx={ctx}
            sorting={{}}
            onSort={NOOP}
            onOpenMember={NOOP}
            readOnly
          />
        </section>
        <section className="tap-team-report-section">
          <h3>{text.team.peopleHeading}</h3>
          <PeopleTable
            rows={current.people}
            copy={copy}
            locale={locale}
            runtimeStatus={view.runtimeStatus}
            readOnly
          />
        </section>
      </article>
    </div>
  );
}

function PrintIcon() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 14 14">
      <g fill="none" stroke="currentColor" strokeWidth="1.2">
        <path d="M3.5 5V1.5h7V5" />
        <rect x="1.5" y="5" width="11" height="5" rx="1.2" />
        <path d="M3.5 8.5h7v4h-7z" fill="#fff" />
      </g>
    </svg>
  );
}
