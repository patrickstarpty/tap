import type { LcaDefinition } from "./client";
export interface RecordingControl {
  controller_id?: string;
  control_state: string;
  control_epoch: number;
}
export interface Recording {
  session_id: string;
  flow_id: string;
  draft_version: number;
  status:
    "starting" | "recording" | "draining" | "stopped" | "canceled" | "unknown";
  control?: RecordingControl;
  watermark?: number;
  outcome_unknown?: boolean;
  projection?: {
    ready: boolean;
    definition: LcaDefinition | null;
    missing_sequences: number[];
    excluded: { action_id: string; sequence: number; reason: string }[];
  };
}
export interface RecordingFrame {
  control_epoch: number;
  frame_sequence: number;
  captured_at: string;
  viewport: { width: number; height: number };
  media_type: "image/jpeg";
  image_base64: string;
}
export interface RecordingGrant {
  capability: string;
  controllerId?: string;
  control: RecordingControl;
  expiresInSeconds?: number;
}
export interface RecordingAction {
  type: "navigate" | "click" | "fill" | "select" | "scroll";
  selector?: string;
  value?: string;
  url?: string;
  x?: number;
  y?: number;
  delta_x?: number;
  delta_y?: number;
  frame_sequence?: number;
  viewport?: { width: number; height: number };
}
export interface ActionReceipt {
  action_id?: string;
  status: string;
}
export interface RecorderClient {
  cancel(
    project: string,
    flow: string,
    session: string,
    key: string,
    capability?: string,
  ): Promise<Recording>;
  list(project: string, flow: string): Promise<Recording[]>;
  start(
    project: string,
    flow: string,
    body: { expected_version: number },
    key: string,
  ): Promise<Recording>;
  get(project: string, flow: string, session: string): Promise<Recording>;
  grant(
    project: string,
    flow: string,
    session: string,
  ): Promise<RecordingGrant>;
  takeover(
    project: string,
    flow: string,
    session: string,
    epoch: number,
    key: string,
    capability: string,
  ): Promise<RecordingControl>;
  frame(
    project: string,
    flow: string,
    session: string,
    capability: string,
  ): Promise<RecordingFrame>;
  action(
    project: string,
    flow: string,
    session: string,
    body: { action: RecordingAction; expected_epoch: number },
    key: string,
    capability: string,
  ): Promise<ActionReceipt>;
  stop(
    project: string,
    flow: string,
    session: string,
    epoch: number,
    key: string,
    capability: string,
  ): Promise<Recording>;
}
export class RecorderError extends Error {
  constructor(
    readonly status: number,
    readonly code?: "live_frame_pending",
  ) {
    super(
      status === 401 || status === 403
        ? "Recording access expired or is unavailable. Refresh the recording; no action was retried."
        : status === 503
          ? "Shared browser recording is unavailable."
          : status === 409
            ? "Recording request was not confirmed. Refresh the existing recording before another action."
            : "Recording request could not be confirmed.",
    );
  }
}
export function createRecorderClient(
  fetcher: typeof fetch = (...args) => fetch(...args),
): RecorderClient {
  const base = (p: string, f: string, s?: string) =>
    `/api/projects/${encodeURIComponent(p)}/lca/flows/${encodeURIComponent(f)}/recordings${s ? `/${encodeURIComponent(s)}` : ""}`;
  async function request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    key?: string,
    capability?: string,
    frameRead = false,
  ): Promise<T> {
    const headers = new Headers({ Accept: "application/json" });
    if (body !== undefined) headers.set("Content-Type", "application/json");
    if (key) headers.set("Idempotency-Key", key);
    if (capability) headers.set("X-LCA-Capability", capability);
    const response = await fetcher(path, {
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      let code: "live_frame_pending" | undefined;
      if (frameRead && response.status === 409) {
        const body = await response.json().catch(() => null);
        if (body?.detail?.code === "live_frame_pending")
          code = "live_frame_pending";
      }
      throw new RecorderError(response.status, code);
    }
    return response.json() as Promise<T>;
  }
  return {
    cancel: (p, f, s, k, c) =>
      request(`${base(p, f, s)}/cancel`, "POST", {}, k, c),
    list: (p, f) => request(base(p, f)),
    start: (p, f, b, k) => request(base(p, f), "POST", b, k),
    get: (p, f, s) => request(base(p, f, s)),
    grant: (p, f, s) => request(`${base(p, f, s)}/capability`, "POST"),
    takeover: (p, f, s, e, k, c) =>
      request(`${base(p, f, s)}/takeover`, "POST", { expected_epoch: e }, k, c),
    frame: (p, f, s, c) =>
      request(`${base(p, f, s)}/frame`, "GET", undefined, undefined, c, true),
    action: (p, f, s, b, k, c) =>
      request(`${base(p, f, s)}/actions`, "POST", b, k, c),
    stop: (p, f, s, e, k, c) =>
      request(`${base(p, f, s)}/stop`, "POST", { expected_epoch: e }, k, c),
  };
}
