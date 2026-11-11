import type { BrowserControlTransport } from "./BrowserControl";

export function createBrowserControlTransport(
  projectId: string,
  taskId: string,
  sessionId: string,
  fetcher: typeof fetch = globalThis.fetch,
): BrowserControlTransport {
  const base = `/api/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(taskId)}/browser-control/${encodeURIComponent(sessionId)}`;
  const origin =
    window.location.origin === "null"
      ? "http://127.0.0.1"
      : window.location.origin;
  async function request<T>(
    path: string,
    signal: AbortSignal,
    capability?: string,
    body?: unknown,
    key?: string,
  ): Promise<T> {
    const response = await fetcher(
      new Request(new URL(base + path, origin), {
        method: path === "/panel" || body !== undefined ? "POST" : "GET",
        credentials: "same-origin",
        cache: "no-store",
        signal,
        headers: {
          ...(capability ? { "X-Browser-Capability": capability } : {}),
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(key ? { "Idempotency-Key": key } : {}),
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      }),
    );
    if (!response.ok)
      throw Object.assign(new Error("Browser control request failed"), {
        status: response.status,
      });
    return (await response.json()) as T;
  }
  return {
    issue: (signal) => request("/panel", signal),
    state: (capability, signal) => request("/state", signal, capability),
    humanAction: (capability, command, key, signal) =>
      request("/actions", signal, capability, command, key),
    frame: async (capability, signal) => {
      let frame: {
        frame_sequence: number;
        captured_at: string;
        control_epoch: number;
        viewport: { width: number; height: number };
        image_base64: string;
        media_type: string;
      };
      try {
        frame = await request("/frame", signal, capability);
      } catch (error) {
        if (
          typeof error === "object" &&
          error &&
          "status" in error &&
          error.status === 409
        )
          return null;
        throw error;
      }
      if (
        frame.media_type !== "image/jpeg" ||
        typeof frame.image_base64 !== "string" ||
        frame.image_base64.length > 666668 ||
        !Number.isSafeInteger(frame.frame_sequence) ||
        !Number.isSafeInteger(frame.control_epoch) ||
        !Number.isFinite(Date.parse(frame.captured_at)) ||
        !frame.viewport ||
        frame.viewport.width <= 0 ||
        frame.viewport.height <= 0
      ) {
        throw new Error("Invalid browser frame");
      }
      return {
        frameSequence: frame.frame_sequence,
        capturedAt: frame.captured_at,
        controlEpoch: frame.control_epoch,
        viewport: frame.viewport,
        imageDataUrl: `data:image/jpeg;base64,${frame.image_base64}`,
      };
    },
    control: (capability, command, key, signal) =>
      request("/commands", signal, capability, command, key),
  };
}
