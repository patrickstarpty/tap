import { useMemo, useState, type ReactNode } from "react";

import {
  agentTeamExportUrl,
  resolvePeriod,
  useTeamReport,
  type AgentTeamMemberRow,
  type AgentTeamReportView,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { TrendChart, type TrendSeries } from "./charts";
import {
  EMPTY,
  formatCost,
  formatCount,
  formatDateTime,
  formatDuration,
  teamLocale,
} from "./format";
import type { CellContext } from "./memberCells";
import { PeopleTable } from "./PeopleTable";
import { PeriodPicker, type PeriodSelection } from "./PeriodPicker";
import { RosterTable } from "./RosterTable";
import { RuntimeBadge } from "./RuntimeBadge";

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
  const buckets = [...runs.keys()].sort();
  if (buckets.length === 0) return [];
  const series = (label: string, source: Map<string, number>) => ({
    label,
    points: buckets.map((bucket) => ({
      bucket,
      value: source.get(bucket) ?? 0,
    })),
  });
  return [series(labels.runs, runs), series(labels.failures, failures)];
}

interface Metric {
  label: string;
  current: number | null;
  previous: number | null;
  format: (value: number) => string;
}

function ChangeCell({ metric }: { metric: Metric }) {
  const { current, previous, format } = metric;
  if (current === null || previous === null || previous === 0)
    return <>{EMPTY}</>;
  const delta = current - previous;
  const percent = Math.round((delta / previous) * 100);
  const arrow = delta > 0 ? "▲" : delta < 0 ? "▼" : "▬";
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
  return (
    <>
      {arrow} {sign}
      {format(Math.abs(delta))} ({sign}
      {Math.abs(percent)}%)
    </>
  );
}

export function ReportTab({ projectId, copy }: ReportTabProps) {
  const text = copy.agentTeam;
  const labels = text.report;
  const locale = teamLocale(copy);
  // Independent of the other tabs: the report defaults to last month.
  const [selection, setSelection] =
    useState<PeriodSelection>(lastMonthSelection);
  const period = useMemo(
    () => resolvePeriod(selection.preset, new Date(), selection.custom),
    [selection],
  );
  const rangeInvalid =
    selection.preset === "custom" &&
    selection.custom.from > selection.custom.to;
  const report = useTeamReport(projectId, period);

  const picker = (
    <div className="tap-team-report-controls">
      <PeriodPicker copy={copy} value={selection} onChange={setSelection} />
      <div className="tap-team-report-actions">
        <button type="button" onClick={() => window.print()}>
          {text.exportPdf}
        </button>
        {rangeInvalid ? null : (
          <a href={agentTeamExportUrl(projectId, period)} download>
            {text.exportCsv}
          </a>
        )}
      </div>
    </div>
  );
  if (rangeInvalid) return <div className="tap-team-report">{picker}</div>;

  const view: AgentTeamReportView | undefined = report.data;
  if (view === undefined) {
    return (
      <div className="tap-team-report">
        {picker}
        {report.isError ? (
          <div role="alert">
            <p>{text.loadFailed}</p>
            <button type="button" onClick={() => void report.refetch()}>
              {text.retry}
            </button>
          </div>
        ) : (
          <p role="status">{text.loading}</p>
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
    },
    {
      label: text.metrics.active,
      current: cs.active,
      previous: ps.active,
      format: count,
    },
    {
      label: text.metrics.idle,
      current: cs.idle,
      previous: ps.idle,
      format: count,
    },
    {
      label: text.metrics.runs,
      current: cs.runs,
      previous: ps.runs,
      format: count,
    },
    {
      label: text.metrics.latency,
      current: cs.latencyTotalMs,
      previous: ps.latencyTotalMs,
      format: (value) => formatDuration(value, locale),
    },
    {
      label: text.metrics.cost,
      current: num(cs.costUsd),
      previous: num(ps.costUsd),
      format: cost,
    },
    {
      label: text.metrics.maintenance,
      current: cs.maintenanceTotal,
      previous: ps.maintenanceTotal,
      format: count,
    },
    {
      label: text.metrics.servedAvg,
      current: cs.servedAvg,
      previous: ps.servedAvg,
      format: count,
    },
    {
      label: text.metrics.ownersAvg,
      current: cs.ownersAvg,
      previous: ps.ownersAvg,
      format: count,
    },
  ];
  const show = (metric: Metric, value: number | null): ReactNode =>
    value === null || Number.isNaN(value) ? EMPTY : metric.format(value);
  const comparedWith = labels.comparedWith
    .replace("{from}", view.data.previousPeriod.from)
    .replace("{to}", view.data.previousPeriod.to);
  const trend = teamTrend(current.members, {
    runs: text.metrics.runs,
    failures: labels.failures,
  });

  return (
    <div className="tap-team-report">
      {picker}
      <header className="tap-team-report-header">
        <h2>{labels.title}</h2>
        <p>
          {labels.project}: {projectId}
        </p>
        <p>
          {labels.period}: {view.period.from} – {view.period.to} (
          {view.timezone})
        </p>
        <p>
          {text.updatedAt
            .replace(
              "{time}",
              formatDateTime(view.computedAt, view.timezone, locale),
            )
            .replace("{timezone}", view.timezone)}
        </p>
        <p>{comparedWith}</p>
        {view.runtimeStatus === "ok" ? null : (
          <RuntimeBadge
            status={view.runtimeStatus}
            computedAt={view.computedAt}
            timezone={view.timezone}
            copy={copy}
          />
        )}
      </header>
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
                <td>{show(metric, metric.current)}</td>
                <td>{show(metric, metric.previous)}</td>
                <td>
                  <ChangeCell metric={metric} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="tap-team-report-section">
        <h3>{labels.trendHeading}</h3>
        {trend.length === 0 ? (
          <p>{labels.noTrend}</p>
        ) : (
          <TrendChart series={trend} unit={text.metrics.runs} locale={locale} />
        )}
      </section>
      <section className="tap-team-report-section">
        <h3>{text.team.rosterHeading}</h3>
        <RosterTable
          rows={current.members}
          ctx={ctx}
          sorting={{}}
          onSort={NOOP}
          onOpenMember={NOOP}
          unscrolled
        />
      </section>
      <section className="tap-team-report-section">
        <h3>{text.team.peopleHeading}</h3>
        <PeopleTable
          rows={current.people}
          copy={copy}
          locale={locale}
          runtimeStatus={view.runtimeStatus}
        />
      </section>
    </div>
  );
}
