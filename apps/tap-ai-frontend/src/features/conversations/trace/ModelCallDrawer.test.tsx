import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderApp } from "../../../shared/testing/renderApp";
import { ModelCallDrawer } from "./ModelCallDrawer";

afterEach(() => {
  vi.unstubAllGlobals();
});

const json = (value: unknown) =>
  new Response(JSON.stringify(value), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

describe("ModelCallDrawer", () => {
  it("loads detail only when opened", async () => {
    const fetcher = vi.fn(async () => {
      throw new Error("should not be called");
    });
    vi.stubGlobal("fetch", fetcher);

    renderApp(
      <ModelCallDrawer
        projectId="project-1"
        callId={null}
        locale="en"
        onClose={() => undefined}
      />,
    );

    expect(fetcher).not.toHaveBeenCalled();
  });

  it("shows request response reasoning tabs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json({
          callId: "call-1",
          attempts: 1,
          createdAt: "2026-09-30T00:00:00Z",
          latencyMs: 320,
          modelName: "gpt-4o",
          operation: "chat",
          request: '{"messages":[]}',
          response: '{"choices":[]}',
          reasoning: "thinking about it",
          status: "ok",
        }),
      ),
    );

    renderApp(
      <ModelCallDrawer
        projectId="project-1"
        callId="call-1"
        locale="en"
        onClose={() => undefined}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText('{"messages":[]}')).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole("tab", { name: "Response" }));
    expect(screen.getByText('{"choices":[]}')).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Reasoning" }));
    expect(screen.getByText("thinking about it")).toBeInTheDocument();
  });
});
