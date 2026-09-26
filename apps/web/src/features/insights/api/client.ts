import type { components } from "../../../shared/api/generated/schema";

export type MetricId = components["schemas"]["MetricId"];
export type MetricResult = components["schemas"]["MetricResultContract"];
export type MetricCompleteness = MetricResult["completeness"];
export type MetricFilters = components["schemas"]["MetricFiltersContract"];
export type MetricQueryRequest = components["schemas"]["MetricQueryRequest"];
export type MetricQueryResponse = components["schemas"]["MetricQueryResponse"];
export type MetricCatalog = components["schemas"]["MetricCatalogContract"];

export interface RunSummary {
  runId: string;
  externalRunId: string;
  sourceId: string;
  buildId: string | null;
  branch: string | null;
  environment: string;
  configuration: string;
  startedAt: string | null;
  instanceCount: number;
  evidenceRefs: string[];
}

export interface FailureDetail {
  factKey: string;
  runId: string;
  externalRunId: string;
  sourceId: string;
  stableTestId: string | null;
  sourceTestIdentity: string;
  dataRow: string | null;
  result: "fail" | "error";
  configuration: string;
  evidenceRefs: string[];
}

export interface AttemptDetail {
  factKey: string;
  externalRunId: string;
  stableTestId: string | null;
  sourceTestIdentity: string;
  dataRow: string | null;
  attempt: number | null;
  result: string;
  durationSeconds: number | null;
  evidenceRefs: string[];
}

export interface Page<T> {
  queryId: string;
  items: T[];
  nextCursor: string | null;
}

export type ReportState =
  | "received"
  | "validating"
  | "mapped"
  | "projecting"
  | "ready"
  | "rejected"
  | "conflicted"
  | "failed";

export interface ReportReceipt {
  receiptId: string;
  projectId: string;
  sourceId: string;
  externalRunId: string;
  batchId: string;
  shardId: string;
  checksum: string;
  parserVersion: string;
  correctionNo: number;
  state: ReportState;
  completeness: "complete" | "partial" | "unknown";
  sizeBytes: number;
  conflictWithReceiptId: string | null;
  failureReason: string | null;
  evidenceUrl: string;
}

export interface ReportManifest {
  projectId: string;
  sourceId: string;
  externalRunId: string;
  batchId: string;
  shardId: string;
  expectedShards: number | null;
  containsCompleteAttempts: boolean;
  applicationCommit: string;
  scriptCommit: string;
  environment: string;
  configuration: string;
  timezone: string;
  correctionNo: number;
  buildId?: string;
  branch?: string;
  startedAt: string;
}

export interface InsightsDataAdapter {
  listMetrics(projectId: string): Promise<MetricCatalog>;
  createQuery(projectId: string, query: MetricQueryRequest): Promise<MetricQueryResponse>;
  getQuery(projectId: string, queryId: string): Promise<MetricQueryResponse>;
  listRuns(projectId: string, queryId: string): Promise<Page<RunSummary>>;
  listFailures(projectId: string, queryId: string): Promise<Page<FailureDetail>>;
  listAttempts(projectId: string, queryId: string, runId: string): Promise<Page<AttemptDetail> & { runId: string }>;
  uploadReport(projectId: string, manifest: ReportManifest, file: File): Promise<ReportReceipt>;
  getReceipt(projectId: string, receiptId: string): Promise<ReportReceipt>;
  retryReceipt(projectId: string, receiptId: string): Promise<ReportReceipt>;
  downloadEvidence(projectId: string, receiptId: string): Promise<Blob>;
  exportQuery(projectId: string, queryId: string): Promise<Blob>;
}

export class InsightsHttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function path(projectId: string, suffix: string) {
  return `/api/v1/projects/${encodeURIComponent(projectId)}/insights${suffix}`;
}

export function createInsightsClient(options: {
  token: () => string | undefined;
  fetch?: typeof window.fetch;
}): InsightsDataAdapter {
  const request = options.fetch ?? window.fetch.bind(window);
  const call = async <T>(url: string, init: RequestInit = {}): Promise<T> => {
    const token = options.token();
    const response = await request(url, {
      ...init,
      credentials: "same-origin",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        // A proxy may return a non-JSON outage page; status still drives UI recovery.
      }
      throw new InsightsHttpError(detail || "Insights request failed", response.status);
    }
    return (await response.json()) as T;
  };
  const collectPages = async <T, P extends Page<T>>(baseUrl: string): Promise<P> => {
    let page = await call<P>(`${baseUrl}&limit=100`);
    const first = page;
    const items = [...page.items];
    const seen = new Set<string>();
    while (page.nextCursor !== null) {
      if (seen.has(page.nextCursor) || seen.size >= 100)
        throw new InsightsHttpError("Insights pagination is invalid", 502);
      seen.add(page.nextCursor);
      page = await call<P>(
        `${baseUrl}&limit=100&cursor=${encodeURIComponent(page.nextCursor)}`,
      );
      if (page.queryId !== first.queryId)
        throw new InsightsHttpError("Insights detail scope changed", 502);
      items.push(...page.items);
    }
    return { ...first, items, nextCursor: null };
  };
  const queryPage = <T>(projectId: string, suffix: string, queryId: string) =>
    collectPages<T, Page<T>>(
      `${path(projectId, suffix)}?queryId=${encodeURIComponent(queryId)}`,
    );

  return {
    listMetrics: (projectId) => call(path(projectId, "/metrics")),
    createQuery: (projectId, query) =>
      call(path(projectId, "/queries"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(query),
      }),
    getQuery: (projectId, queryId) =>
      call(path(projectId, `/queries/${encodeURIComponent(queryId)}`)),
    listRuns: (projectId, queryId) => queryPage(projectId, "/runs", queryId),
    listFailures: (projectId, queryId) => queryPage(projectId, "/failures", queryId),
    listAttempts: (projectId, queryId, runId) =>
      collectPages<AttemptDetail, Page<AttemptDetail> & { runId: string }>(
        `${path(projectId, `/runs/${encodeURIComponent(runId)}/attempts`)}?queryId=${encodeURIComponent(queryId)}`,
      ),
    uploadReport: (projectId, manifest, file) =>
      call(path(projectId, "/reports"), {
        method: "POST",
        headers: {
          "Content-Type": "application/xml",
          "X-TAP-Report-Manifest": JSON.stringify(manifest),
        },
        body: file,
      }),
    getReceipt: (projectId, receiptId) =>
      call(path(projectId, `/reports/${encodeURIComponent(receiptId)}`)),
    retryReceipt: (projectId, receiptId) =>
      call(path(projectId, `/reports/${encodeURIComponent(receiptId)}/retry`), {
        method: "POST",
      }),
    downloadEvidence: async (projectId, receiptId) => {
      const token = options.token();
      const response = await request(
        path(projectId, `/evidence/${encodeURIComponent(receiptId)}`),
        {
          credentials: "same-origin",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      if (!response.ok) throw new InsightsHttpError("Evidence request failed", response.status);
      return response.blob();
    },
    exportQuery: async (projectId, queryId) => {
      const [query, runs, failures] = await Promise.all([
        call<MetricQueryResponse>(path(projectId, `/queries/${encodeURIComponent(queryId)}`)),
        queryPage<RunSummary>(projectId, "/runs", queryId),
        queryPage<FailureDetail>(projectId, "/failures", queryId),
      ]);
      return new Blob([JSON.stringify({ query, runs, failures }, null, 2)], {
        type: "application/json",
      });
    },
  };
}
