import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  AttemptDetail,
  FailureDetail,
  InsightsDataAdapter,
  MetricCatalog,
  MetricFilters,
  MetricId,
  MetricQueryResponse,
  MetricResult,
  ReportReceipt,
  RunSummary,
} from "../api/client";
import { ReportIntake } from "./ReportIntake";
import type { ReceiptEventSource } from "./ReportIntake";
import { RunDetails } from "./RunDetails";
import "./insights.css";

const METRICS: MetricId[] = [
  "first_pass_rate",
  "final_pass_rate",
  "retry_recovery_rate",
  "recovery_contribution_rate",
  "skipped_count",
  "p95_duration_seconds",
];

interface ScopeDraft {
  from: string;
  to: string;
  build: string;
  branch: string;
  environment: string;
}

function defaultScope(): ScopeDraft {
  const to = new Date();
  to.setUTCDate(to.getUTCDate() + 1);
  const from = new Date(to);
  from.setUTCDate(from.getUTCDate() - 30);
  return {
    from: from.toISOString().slice(0, 10),
    to: to.toISOString().slice(0, 10),
    build: "",
    branch: "",
    environment: "all",
  };
}

function status(error: unknown) {
  return (error as { status?: number }).status;
}

function errorCopy(error: unknown) {
  if (status(error) === 401) return "Sign in to view project insights.";
  if (status(error) === 403)
    return "You do not have access to this project's insights.";
  if (status(error) === 404) return "The saved query no longer exists.";
  return "Insights are temporarily unavailable.";
}

function metric(query: MetricQueryResponse, id: MetricId) {
  return query.metrics.find((item) => item.metricId === id);
}

function unavailableCopy(value: MetricResult | undefined) {
  if (!value || value.completeness === "empty") return "No eligible data";
  if (value.missingReasons.includes("missing-first-attempt-history"))
    return "First-attempt history is unavailable.";
  if (value.missingReasons.includes("missing-run-start-time"))
    return "Run time is missing.";
  return "Metric unavailable";
}

function formatMetric(value: MetricResult | undefined) {
  if (!value || value.value === null || value.completeness !== "complete")
    return "—";
  if (value.metricId === "p95_duration_seconds")
    return `${value.value.toFixed(2)} s`;
  if (value.metricId === "skipped_count") return String(value.value);
  return `${(value.value * 100).toFixed(2)}%`;
}

function queryFilters(scope: ScopeDraft): MetricFilters {
  return {
    sourceIds: [],
    runIds: [],
    buildIds: scope.build ? [scope.build] : [],
    branches: scope.branch ? [scope.branch] : [],
    environments:
      scope.environment === "all" ? [] : [scope.environment],
    configurations: [],
  };
}

export function InsightsWorkspace({
  adapter,
  projectId,
  initialQueryId,
  tapperBaseUrl = "http://127.0.0.1:5173/",
}: {
  adapter: InsightsDataAdapter;
  projectId: string;
  initialQueryId?: string;
  tapperBaseUrl?: string;
}) {
  const [catalog, setCatalog] = useState<MetricCatalog | null>(null);
  const [result, setResult] = useState<{
    query: MetricQueryResponse;
    runs: RunSummary[];
    failures: FailureDetail[];
  } | null>(null);
  const query = result?.query ?? null;
  const runs = result?.runs ?? [];
  const failures = result?.failures ?? [];
  const [scope, setScope] = useState(defaultScope);
  const [draft, setDraft] = useState(scope);
  const [tableFilter, setTableFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedRun, setSelectedRun] = useState<RunSummary | null>(null);
  const [attempts, setAttempts] = useState<AttemptDetail[]>([]);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const mounted = useRef(false);
  const loadGeneration = useRef(0);
  const attemptGeneration = useRef(0);
  const receiptKey = `tap.insights.receipts.${projectId}`;
  const queryKey = `tap.insights.query.${projectId}`;
  const [knownReceiptIds, setKnownReceiptIds] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(receiptKey) ?? "[]") as string[];
    } catch {
      return [];
    }
  });

  const loadDetails = useCallback(
    async (current: MetricQueryResponse) => {
      const [runPage, failurePage] = await Promise.all([
        adapter.listRuns(projectId, current.queryId),
        adapter.listFailures(projectId, current.queryId),
      ]);
      if (
        runPage.queryId !== current.queryId ||
        failurePage.queryId !== current.queryId
      )
        throw new Error("Insights detail scope mismatch");
      return { runs: runPage.items, failures: failurePage.items };
    },
    [adapter, projectId],
  );

  const create = useCallback(
    async (nextScope: ScopeDraft, generation: number) => {
      const next = await adapter.createQuery(projectId, {
        metricIds: METRICS,
        filters: queryFilters(nextScope),
        from: nextScope.from,
        to: nextScope.to,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
        asOf: new Date().toISOString(),
      });
      if (!mounted.current || generation !== loadGeneration.current) return;
      const details = await loadDetails(next);
      if (!mounted.current || generation !== loadGeneration.current) return;
      localStorage.setItem(queryKey, next.queryId);
      setResult({ query: next, ...details });
      setScope(nextScope);
    },
    [adapter, loadDetails, projectId, queryKey],
  );

  const beginLoad = useCallback(() => {
    const generation = ++loadGeneration.current;
    ++attemptGeneration.current;
    setSelectedRun(null);
    setAttempts([]);
    setDetailsLoading(false);
    setLoading(true);
    setError("");
    return generation;
  }, []);

  const initialize = useCallback(async () => {
    const generation = beginLoad();
    try {
      const definitions = await adapter.listMetrics(projectId);
      if (!mounted.current || generation !== loadGeneration.current) return;
      setCatalog(definitions);
      const saved =
        initialQueryId ?? localStorage.getItem(queryKey) ?? undefined;
      if (saved) {
        try {
          const restored = await adapter.getQuery(projectId, saved);
          if (!mounted.current || generation !== loadGeneration.current) return;
          const details = await loadDetails(restored);
          if (!mounted.current || generation !== loadGeneration.current) return;
          const restoredScope = {
            from: restored.from,
            to: restored.to,
            build: restored.filters.buildIds?.[0] ?? "",
            branch: restored.filters.branches?.[0] ?? "",
            environment: restored.filters.environments?.[0] ?? "all",
          };
          setResult({ query: restored, ...details });
          setScope(restoredScope);
          setDraft(restoredScope);
          return;
        } catch (cause) {
          if (!mounted.current || generation !== loadGeneration.current) return;
          if (status(cause) !== 404) throw cause;
          localStorage.removeItem(queryKey);
        }
      }
      await create(scope, generation);
    } catch (cause) {
      if (mounted.current && generation === loadGeneration.current)
        setError(errorCopy(cause));
    } finally {
      if (mounted.current && generation === loadGeneration.current)
        setLoading(false);
    }
  }, [adapter, beginLoad, create, initialQueryId, loadDetails, projectId, queryKey, scope]);

  useEffect(() => {
    mounted.current = true;
    void initialize();
    return () => {
      mounted.current = false;
      ++loadGeneration.current;
      ++attemptGeneration.current;
    };
    // Initial server restore is intentionally a one-time lifecycle action.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyFilters = async () => {
    const generation = beginLoad();
    try {
      await create(draft, generation);
    } catch (cause) {
      if (mounted.current && generation === loadGeneration.current)
        setError(errorCopy(cause));
    } finally {
      if (mounted.current && generation === loadGeneration.current)
        setLoading(false);
    }
  };

  const openRun = async (run: RunSummary) => {
    if (!query) return;
    const generation = ++attemptGeneration.current;
    const queryGeneration = loadGeneration.current;
    setSelectedRun(run);
    setAttempts([]);
    setDetailsLoading(true);
    try {
      const page = await adapter.listAttempts(
        projectId,
        query.queryId,
        run.runId,
      );
      if (page.queryId !== query.queryId || page.runId !== run.runId)
        throw new Error("Insights attempt scope mismatch");
      if (
        !mounted.current ||
        generation !== attemptGeneration.current ||
        queryGeneration !== loadGeneration.current
      ) return;
      setAttempts(page.items);
    } catch (cause) {
      if (
        mounted.current &&
        generation === attemptGeneration.current &&
        queryGeneration === loadGeneration.current
      )
        setError(errorCopy(cause));
    } finally {
      if (
        mounted.current &&
        generation === attemptGeneration.current &&
        queryGeneration === loadGeneration.current
      )
        setDetailsLoading(false);
    }
  };

  const recordReceipt = (receipt: ReportReceipt, source: ReceiptEventSource) => {
    if (!knownReceiptIds.includes(receipt.receiptId)) {
      const next = [...knownReceiptIds, receipt.receiptId];
      setKnownReceiptIds(next);
      localStorage.setItem(receiptKey, JSON.stringify(next));
    }
    if (receipt.state === "ready" && source === "updated") void applyFilters();
  };

  const filteredRuns = useMemo(() => {
    const needle = tableFilter.trim().toLowerCase();
    if (!needle) return runs;
    return runs.filter((run) =>
      [run.externalRunId, run.sourceId, run.buildId, run.branch, run.environment]
        .filter(Boolean)
        .some((value) => value!.toLowerCase().includes(needle)),
    );
  }, [runs, tableFilter]);

  const groupedFailures = useMemo(
    () =>
      ["fail", "error"].map((result) => ({
        result,
        count: failures.filter((item) => item.result === result).length,
      })).filter((item) => item.count > 0),
    [failures],
  );

  const incompleteCoverage = query?.reportCoverage.filter(
    (item) => item.completeness !== "complete",
  ) ?? [];

  const exportCurrent = async () => {
    if (!query) return;
    try {
      const blob = await adapter.exportQuery(projectId, query.queryId);
      if (typeof URL.createObjectURL !== "function") return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `tap-insights-${query.queryId}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      setError(errorCopy(cause));
    }
  };

  const copyLink = async () => {
    if (!query) return;
    const url = new URL(window.location.href);
    url.searchParams.set("module", "test-analytics");
    url.searchParams.set("queryId", query.queryId);
    await navigator.clipboard.writeText(url.toString());
  };

  return (
    <section className="ti-workspace">
      <header className="ti-header">
        <div><h1>Test Insights</h1><p>Project · <strong>{projectId}</strong></p></div>
        <div className="ti-actions">
          <button type="button" disabled={!query} onClick={exportCurrent}>Export current query</button>
          <button type="button" disabled={!query} onClick={copyLink}>Copy project link</button>
        </div>
      </header>

      <ReportIntake adapter={adapter} projectId={projectId} knownReceiptIds={knownReceiptIds} onReceipt={recordReceipt} />

      <form className="ti-filters" onSubmit={(event) => { event.preventDefault(); void applyFilters(); }}>
        <label>From<input type="date" value={draft.from} onChange={(event) => setDraft({ ...draft, from: event.target.value })} /></label>
        <label>To<input type="date" value={draft.to} onChange={(event) => setDraft({ ...draft, to: event.target.value })} /></label>
        <label>Build<input value={draft.build} onChange={(event) => setDraft({ ...draft, build: event.target.value })} /></label>
        <label>Branch<input value={draft.branch} onChange={(event) => setDraft({ ...draft, branch: event.target.value })} /></label>
        <label>Environment<select value={draft.environment} onChange={(event) => setDraft({ ...draft, environment: event.target.value })}><option value="all">All</option><option value="qa">QA</option><option value="staging">Staging</option><option value="production">Production</option></select></label>
        <button className="ti-primary" type="submit">Apply filters</button>
      </form>

      {loading ? <section className="ti-loading" aria-label="Loading insights"><i /><i /><i /></section> : null}
      {error ? <section className="ti-error" role="alert"><strong>{error}</strong><button type="button" onClick={() => void initialize()}>Try again</button></section> : null}
      {!loading && !error && query ? (
        <>
          <div className="ti-query-meta">Query {query.queryId} · {query.metricVersion} · facts through {query.factWatermark.visibleDataVersion} · as of {new Date(query.asOf).toLocaleString()}</div>
          {incompleteCoverage.length ? (
            <section className="ti-coverage" role="status" aria-label="Report coverage">
              <strong>Report coverage affects these metrics.</strong>
              <ul>{incompleteCoverage.map((item) => (
                <li key={`${item.sourceId}:${item.externalRunId}:${item.reportBatchId}`}>
                  {item.completeness === "partial" && item.expectedShards !== null
                    ? `Partial report coverage: ${item.receivedShards} of ${item.expectedShards} shards received`
                    : "Report coverage unknown: expected shard count is unavailable"}
                  {` for ${item.sourceId} / ${item.externalRunId} (batch ${item.reportBatchId}).`}
                </li>
              ))}</ul>
            </section>
          ) : null}
          <section className="ti-metrics" aria-label="Authorized metrics">
            {[
              ["first_pass_rate", "First-pass rate"],
              ["final_pass_rate", "Final pass rate"],
              ["retry_recovery_rate", "Retry recovery"],
              ["recovery_contribution_rate", "Recovery contribution"],
              ["skipped_count", "Skipped"],
              ["p95_duration_seconds", "P95 duration"],
            ].map(([id, label]) => {
              const value = metric(query, id as MetricId);
              return <article key={id}><span>{label}</span><strong>{formatMetric(value)}</strong>{value?.value === null ? <small>{unavailableCopy(value)}</small> : value?.denominator !== null ? <small>{value?.numerator ?? "—"} / {value?.denominator ?? "—"}</small> : null}</article>;
            })}
          </section>
          <details className="ti-definitions"><summary>Metric definitions</summary><dl>{catalog?.items.map((item) => <div key={item.metricId}><dt>{item.label}</dt><dd>{item.definition}</dd></div>)}</dl></details>

          <section className="ti-trends" aria-labelledby="ti-trends-heading">
            <h2 id="ti-trends-heading">Trends</h2>
            {query.trends.length ? <div className="ti-trend-grid">{query.trends.map((point) => <article key={point.localDate}><strong>{point.localDate}</strong><span>First {formatMetric(point.metrics.find((item) => item.metricId === "first_pass_rate"))}</span><span>Final {formatMetric(point.metrics.find((item) => item.metricId === "final_pass_rate"))}</span><span>P95 {formatMetric(point.metrics.find((item) => item.metricId === "p95_duration_seconds"))}</span></article>)}</div> : <p>No trend points are available for this query.</p>}
          </section>

          <div className="ti-data-grid">
            <section>
              <div className="ti-section-heading"><div><h2>Runs</h2><p>Uses the same authorized query. The field below filters this table only.</p></div><label>Filter this table only<input value={tableFilter} onChange={(event) => setTableFilter(event.target.value)} /></label></div>
              {runs.length === 0 ? <p className="ti-empty">No runs match this authorized query scope.</p> : filteredRuns.length === 0 ? <p className="ti-empty">No runs match this table-only filter.</p> : <><small className="ti-scroll-hint">Scroll horizontally on narrow screens to reach run actions.</small><div className="ti-table-scroll" role="region" aria-label="Scrollable runs table" tabIndex={0}><table aria-label="Runs"><thead><tr><th>Run</th><th>Build / branch</th><th>Environment</th><th>Instances</th><th>Action</th></tr></thead><tbody>{filteredRuns.map((run) => <tr key={run.runId}><td>{run.externalRunId}</td><td>{run.buildId ?? "—"} / {run.branch ?? "—"}</td><td>{run.environment}</td><td>{run.instanceCount}</td><td><button type="button" onClick={() => void openRun(run)}>Open {run.externalRunId}</button></td></tr>)}</tbody></table></div></>}
            </section>
            <section><h2>Failure groups</h2>{groupedFailures.length ? <table aria-label="Failure groups"><thead><tr><th>Final result</th><th>Instances</th></tr></thead><tbody>{groupedFailures.map((group) => <tr key={group.result}><td>{group.result}</td><td>{group.count}</td></tr>)}</tbody></table> : <p className="ti-empty">No final failures in this query scope.</p>}</section>
          </div>
          {selectedRun ? detailsLoading ? <section className="ti-loading" aria-label="Loading run details"><i /><i /></section> : <RunDetails adapter={adapter} projectId={projectId} queryId={query.queryId} tapperBaseUrl={tapperBaseUrl} run={selectedRun} attempts={attempts} onClose={() => { ++attemptGeneration.current; setSelectedRun(null); }} /> : null}
        </>
      ) : null}
    </section>
  );
}
