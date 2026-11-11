import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LcaEmbedPage } from "./LcaEmbedPage";
vi.mock("../features/runtime/api/queries", () => ({
  useRuntimeModeQuery: () => ({
    isSuccess: true,
    data: {
      projectId: "project-1",
      actorId: "actor-1",
      identityMode: "validation",
    },
  }),
}));
vi.mock("../widgets/tap/TapperWorkspace", () => ({
  TapperWorkspace: () => <p>Real composer enabled</p>,
}));
const context = {
  available: true,
  project_id: "project-1",
  actor_id: "actor-1",
  identity_mode: "validation",
  gateway_instance_id: "gateway-1",
  context_binding: "a".repeat(64),
};
const init = {
  type: "lca:init",
  schema_version: "lca-assistant-v1",
  nonce: "nonce-12345678901234567890",
  context,
};
function message(
  data: unknown,
  origin = "http://127.0.0.1:5174",
  source: MessageEventSource | null = window.parent,
) {
  act(() =>
    window.dispatchEvent(new MessageEvent("message", { data, origin, source })),
  );
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});
it("keeps execution unavailable until exact parent, origin, nonce and independently resolved context match", async () => {
  vi.stubEnv("VITE_TAP_WEB_URL", "http://127.0.0.1:5174");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(context), { status: 200 }),
  );
  render(<LcaEmbedPage />);
  expect(screen.queryByText("Real composer enabled")).toBeNull();
  message(init, "https://forged.test");
  message(init, undefined, null);
  message({ ...init, nonce: "short" });
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.queryByText("Real composer enabled")).toBeNull();
  message(init);
  expect(await screen.findByText("Real composer enabled")).toBeVisible();
  message(init);
  expect(screen.getAllByText("Real composer enabled")).toHaveLength(1);
});
it("fails closed when another gateway has the same project and actor", async () => {
  vi.stubEnv("VITE_TAP_WEB_URL", "http://127.0.0.1:5174");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({ ...context, gateway_instance_id: "other-gateway" }),
      { status: 200 },
    ),
  );
  render(<LcaEmbedPage />);
  message(init);
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(/context.*match/i),
  );
  expect(screen.queryByText("Real composer enabled")).toBeNull();
});
it("reports a disconnected API without mounting the composer", async () => {
  vi.stubEnv("VITE_TAP_WEB_URL", "http://127.0.0.1:5174");
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
  render(<LcaEmbedPage />);
  message(init);
  expect(await screen.findByRole("alert")).toHaveTextContent(/unavailable/i);
  expect(screen.queryByText("Real composer enabled")).toBeNull();
});
