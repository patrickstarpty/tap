/** Contracts mirror the TAP LCA API. No service credential enters the UI. */
export type Scalar = string | number | boolean;
export type Operation =
  | "upload_file"
  | "download_file"
  | "open_page"
  | "switch_page"
  | "close_page"
  | "click_popup"
  | "click_dialog"
  | "navigate"
  | "click"
  | "fill"
  | "select"
  | "assert_text_exact"
  | "assert_text"
  | "assert_url"
  | "scroll"
  | "scroll_into_view"
  | "press"
  | "hover"
  | "double_click"
  | "right_click"
  | "check"
  | "uncheck"
  | "drag_drop"
  | "wait_for"
  | "assert_visible"
  | "assert_value"
  | "assert_checked"
  | "assert_read";
export interface LcaTarget {
  id: string;
  kind: "test_id" | "role" | "css";
  value: string;
  name?: string | null;
  review_required?: boolean;
  frame_target_id?: string | null;
}
export interface LcaParameter {
  name: string;
  type: "string" | "integer" | "number" | "boolean";
  required: boolean;
  default?: Scalar;
}
export interface LcaStep {
  id: string;
  operation: Operation;
  arguments: Record<string, Scalar | { parameter: string }>;
  target_id?: string | null;
  page_id?: string | null;
  timeout_ms: number;
  provenance?: Record<string, string>;
}
export interface BddStep {
  id: string;
  keyword: "Given" | "When" | "Then" | "And" | "But";
  description: string;
  action_ids: string[];
}
export interface LcaDefinition {
  schema_version: "lca-debug-v1";
  allowed_origins: string[];
  targets: LcaTarget[];
  parameters: LcaParameter[];
  steps: LcaStep[];
  bdd_steps?: BddStep[];
}
export interface LcaFlow {
  id: string;
  project_id: string;
  title: string;
  version: number;
  definition: LcaDefinition;
  created_at?: string;
  updated_at?: string;
}
export interface LcaEvidence {
  kind: string;
  object_ref: string;
  sha256?: string;
  size_bytes?: number;
}
export interface LcaExecution {
  status: string;
  failure_code?: string | null;
  result?: unknown;
  steps?: Array<{
    canonical_step_id?: string;
    title?: string;
    status: string;
    error?: string;
  }>;
  evidence?: LcaEvidence[];
}
export interface LcaRun {
  id: string;
  flow_id: string;
  snapshot_id: string;
  definition_digest: string;
  input_digest: string;
  bundle_id: string;
  execution_id: string | null;
  classification: "debug" | "published";
  canonical_results?: Array<{
    step_id: string;
    status: "passed" | "failed" | "not_run";
  }> | null;
  canonical_results_error?: string | null;
  definition_snapshot?: {
    version: number;
    definition: LcaDefinition;
    digest: string;
    generator_version: string;
  };
  created_at?: string;
  execution?: LcaExecution | null;
}
export interface RunInput {
  expected_version: number;
  parameters: Record<string, Scalar>;
}
export interface LcaRelease {
  id: string;
  flow_id: string;
  version: number;
  created_at: string;
  definition_digest: string;
  verified_run_id: string;
}
export interface LcaAsset {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
}
export interface LcaClient {
  listAssets(project: string): Promise<LcaAsset[]>;
  uploadAsset(
    project: string,
    body: { filename: string; content_base64: string },
    key: string,
  ): Promise<LcaAsset>;
  getRelease(
    project: string,
    id: string,
  ): Promise<LcaRelease & { definition: LcaDefinition }>;
  listReleases(project: string, flow: string): Promise<LcaRelease[]>;
  publish(
    project: string,
    flow: string,
    body: { expected_version: number; verified_run_id: string },
    key: string,
  ): Promise<LcaRelease>;
  createPublishedRun(
    project: string,
    release: string,
    body: { parameters: Record<string, Scalar> },
    key: string,
  ): Promise<LcaRun>;
  listFlows(project: string): Promise<LcaFlow[]>;
  getFlow(project: string, id: string): Promise<LcaFlow>;
  createFlow(
    project: string,
    body: { title: string; definition: LcaDefinition },
    key: string,
  ): Promise<LcaFlow>;
  saveDraft(
    project: string,
    id: string,
    body: { expected_version: number; definition: LcaDefinition },
  ): Promise<LcaFlow>;
  listRuns(project: string, flow: string): Promise<LcaRun[]>;
  createRun(
    project: string,
    flow: string,
    body: RunInput,
    key: string,
  ): Promise<LcaRun>;
  reconcileRun(project: string, id: string): Promise<LcaRun>;
  cancelRun(project: string, id: string): Promise<LcaExecution>;
  getEvidence(project: string, run: string, evidence: string): Promise<Blob>;
  getEvidenceDownload(
    project: string,
    run: string,
    evidence: string,
  ): Promise<{ blob: Blob; filename: string | null }>;
  getRun(project: string, id: string): Promise<LcaRun>;
}
export class LcaHttpError extends Error {
  constructor(readonly status: number) {
    super(
      status === 409
        ? "The saved version changed. Your edits are preserved; reload before saving again."
        : status === 401 || status === 403
          ? "You do not have access to this project's debug flows."
          : status === 404
            ? "This debug flow or run is no longer available."
            : status === 422
              ? "The definition or parameters are invalid. Check targets, values, and allowed origins."
              : status === 503
                ? "LCA capability is unavailable. The authorized application gateway must be connected."
                : "The LCA request could not be confirmed. Refresh history before creating another run.",
    );
  }
}
export function createLcaClient(
  fetcher: typeof fetch = (...args) => fetch(...args),
): LcaClient {
  const base = (project: string) =>
    `/api/projects/${encodeURIComponent(project)}/lca`;
  async function request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    key?: string,
  ): Promise<T> {
    const headers = new Headers({ Accept: "application/json" });
    if (body !== undefined) headers.set("Content-Type", "application/json");
    if (key) headers.set("Idempotency-Key", key);
    const response = await fetcher(path, {
      method,
      headers,
      credentials: "same-origin",
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) throw new LcaHttpError(response.status);
    return response.json() as Promise<T>;
  }
  async function evidenceResponse(
    project: string,
    run: string,
    evidence: string,
  ) {
    const response = await fetcher(
      `${base(project)}/runs/${encodeURIComponent(run)}/evidence/${encodeURIComponent(evidence)}`,
      { credentials: "same-origin", cache: "no-store" },
    );
    if (!response.ok) throw new LcaHttpError(response.status);
    return response;
  }
  return {
    listAssets: (p) => request(`${base(p)}/assets`),
    uploadAsset: (p, body, key) =>
      request(`${base(p)}/assets`, "POST", body, key),
    listFlows: (p) => request(`${base(p)}/flows`),
    getFlow: (p, id) => request(`${base(p)}/flows/${encodeURIComponent(id)}`),
    createFlow: (p, body, key) =>
      request(`${base(p)}/flows`, "POST", body, key),
    saveDraft: (p, id, body) =>
      request(`${base(p)}/flows/${encodeURIComponent(id)}/draft`, "PUT", body),
    listRuns: (p, id) =>
      request(`${base(p)}/flows/${encodeURIComponent(id)}/runs`),
    createRun: (p, id, body, key) =>
      request(
        `${base(p)}/flows/${encodeURIComponent(id)}/runs`,
        "POST",
        body,
        key,
      ),
    reconcileRun: (p, id) =>
      request(`${base(p)}/runs/${encodeURIComponent(id)}/reconcile`, "POST"),
    cancelRun: (p, id) =>
      request(`${base(p)}/runs/${encodeURIComponent(id)}/cancel`, "POST"),
    getEvidence: async (p, run, evidence) =>
      (await evidenceResponse(p, run, evidence)).blob(),
    getEvidenceDownload: async (p, run, evidence) => {
      const response = await evidenceResponse(p, run, evidence);
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const match = /(?:^|;)\s*filename=(?:"([^"]+)"|([^;\s]+))(?:;|$)/i.exec(
        disposition,
      );
      const candidate = match?.[1] ?? match?.[2] ?? "";
      // Preserve the server's bounded ASCII filename; never turn a path or
      // control-bearing header into a local filename.
      const filename = /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,159}$/.test(candidate)
        ? candidate
        : null;
      return { blob: await response.blob(), filename };
    },
    getRelease: (p, id) =>
      request(`${base(p)}/releases/${encodeURIComponent(id)}`),
    listReleases: (p, flow) =>
      request(`${base(p)}/flows/${encodeURIComponent(flow)}/releases`),
    publish: (p, flow, body, key) =>
      request(
        `${base(p)}/flows/${encodeURIComponent(flow)}/releases`,
        "POST",
        body,
        key,
      ),
    createPublishedRun: (p, release, body, key) =>
      request(
        `${base(p)}/releases/${encodeURIComponent(release)}/runs`,
        "POST",
        body,
        key,
      ),
    getRun: (p, id) => request(`${base(p)}/runs/${encodeURIComponent(id)}`),
  };
}
