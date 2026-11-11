import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";

const contextIdentity = {
  actor_id: "actor1",
  identity_mode: "validation",
  gateway_instance_id: "gateway-1",
  context_binding: "a".repeat(64),
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});
it.each(["/", "/?module=low-code", "/lca"])(
  "opens real LCA inside the TAP product shell at %s",
  async (path) => {
    window.history.replaceState(null, "", path);
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        calls.push(url);
        return new Response(
          JSON.stringify(
            url === "/api/lca/context"
              ? {
                  project_id: "authorized-project",
                  available: true,
                  ...contextIdentity,
                }
              : [],
          ),
        );
      }),
    );
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Product" })).toBeVisible();
    expect(
      await screen.findByRole("heading", { name: "Low Code Automation" }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Low Code Automation" }),
    ).toHaveAttribute("aria-current", "page");
    await waitFor(() =>
      expect(calls).toContain("/api/projects/authorized-project/lca/flows"),
    );
    expect(
      screen.queryByText("Life insurance application automation"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Real debug flows" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Simulated prototype")).not.toBeInTheDocument();
  },
);
it("never substitutes simulated success when the backend is unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 503 })),
  );
  render(<App />);
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(
    screen.queryByText("Life insurance application automation"),
  ).not.toBeInTheDocument();
});
it("preserves unsaved LCA edits when switching platform modules", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (url: string) =>
        new Response(
          JSON.stringify(
            url === "/api/lca/context"
              ? { project_id: "p", available: true, ...contextIdentity }
              : [],
          ),
        ),
    ),
  );
  const user = userEvent.setup();
  render(<App />);
  await user.click(
    await screen.findByRole("button", { name: "New debug flow" }),
  );
  await user.type(screen.getByLabelText("Flow title"), "Keep my draft");
  await user.click(screen.getByRole("button", { name: "Test Insights" }));
  await user.click(screen.getByRole("button", { name: "Low Code Automation" }));
  expect(screen.getByLabelText("Flow title")).toHaveValue("Keep my draft");
});
