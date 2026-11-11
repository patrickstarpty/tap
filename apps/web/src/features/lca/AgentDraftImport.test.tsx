import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentDraftImport } from "./AgentDraftImport";
const definition = {
  schema_version: "lca-debug-v1",
  allowed_origins: ["https://example.com"],
  targets: [],
  parameters: [],
  steps: [
    {
      id: "open",
      operation: "navigate",
      arguments: { url: "https://example.com" },
      timeout_ms: 1000,
    },
  ],
};
const result = {
  schema_version: "aa-lca-draft-v1",
  complete: true,
  definition,
  issues: [],
  source: {
    task_id: "task1",
    execution_id: "run1",
    bundle_id: "bundle1",
    input_sha256: "a".repeat(64),
  },
  review_required: true,
};
afterEach(() => vi.unstubAllGlobals());
it("previews without saving or executing, then explicitly loads an unsaved draft", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => result });
  vi.stubGlobal("fetch", fetcher);
  const load = vi.fn();
  render(<AgentDraftImport projectId="p1" onLoad={load} disabled={false} />);
  fireEvent.change(screen.getByLabelText("AA task ID"), {
    target: { value: "task1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Preview AA task" }));
  await screen.findByText(/1 actions/);
  expect(load).not.toHaveBeenCalled();
  expect(fetcher.mock.calls[0][0]).toBe(
    "/api/projects/p1/lca/agent-tasks/task1/preview",
  );
  fireEvent.click(screen.getByRole("button", { name: "Use as unsaved draft" }));
  expect(load).toHaveBeenCalledWith(definition, "AA task task1");
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("shows unsupported actions and cannot load an incomplete projection", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...result,
        complete: false,
        definition: null,
        issues: [
          {
            action_id: "a1",
            sequence: 1,
            operation: "read",
            reason: "unsupported_read_condition",
          },
        ],
      }),
    }),
  );
  render(<AgentDraftImport projectId="p1" onLoad={vi.fn()} disabled={false} />);
  fireEvent.change(screen.getByLabelText("AA task ID"), {
    target: { value: "task1" },
  });
  fireEvent.click(screen.getByText("Preview AA task"));
  await screen.findByText(/unsupported_read_condition/);
  expect(screen.queryByText("Use as unsaved draft")).toBeNull();
});
it("rejects a response for another source task", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...result,
        source: { ...result.source, task_id: "other" },
      }),
    }),
  );
  render(<AgentDraftImport projectId="p1" onLoad={vi.fn()} disabled={false} />);
  fireEvent.change(screen.getByLabelText("AA task ID"), {
    target: { value: "task1" },
  });
  fireEvent.click(screen.getByText("Preview AA task"));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(
      "could not be verified",
    ),
  );
  expect(screen.queryByText("Use as unsaved draft")).toBeNull();
});

it("rejects a reviewed preview that would lose its frozen contract on import", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        ...result,
        contract_review_status: "reviewed",
        acceptance_contract: { version: 1, reviewed: true, fields: [] },
      }),
    }),
  );
  render(<AgentDraftImport projectId="p1" onLoad={vi.fn()} disabled={false} />);
  fireEvent.change(screen.getByLabelText("AA task ID"), {
    target: { value: "task1" },
  });
  fireEvent.click(screen.getByText("Preview AA task"));
  await screen.findByText("AA source could not be verified.");
  expect(screen.queryByText("Use as unsaved draft")).toBeNull();
});

it("accepts a controlled source for read-only preview but never imports on delivery", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue({ ok: true, json: async () => result });
  vi.stubGlobal("fetch", fetcher);
  const load = vi.fn();
  render(
    <AgentDraftImport
      projectId="p1"
      sourceReference={result.source}
      onLoad={load}
      disabled={false}
    />,
  );
  expect(screen.getByLabelText("AA task ID")).toHaveValue("task1");
  expect(screen.getByLabelText("AA task ID")).toHaveAttribute("readonly");
  expect(fetcher).not.toHaveBeenCalled();
  expect(load).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Preview AA task" }));
  await screen.findByText(/1 actions/);
  expect(load).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Use as unsaved draft" }));
  expect(load).toHaveBeenCalledExactlyOnceWith(definition, "AA task task1");
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it.each(["task_id", "execution_id", "bundle_id", "input_sha256"] as const)(
  "rejects authoritative preview with a different controlled %s",
  async (field) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          ...result,
          source: {
            ...result.source,
            [field]: field === "input_sha256" ? "b".repeat(64) : "other",
          },
        }),
      }),
    );
    render(
      <AgentDraftImport
        projectId="p1"
        sourceReference={result.source}
        onLoad={vi.fn()}
        disabled={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Preview AA task" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "could not be verified",
    );
    expect(
      screen.queryByRole("button", { name: "Use as unsaved draft" }),
    ).not.toBeInTheDocument();
  },
);
it.each(["source", "project", "cleared", "locked"])(
  "aborts and discards a late controlled preview when %s changes",
  async (change) => {
    let resolve!: (value: unknown) => void;
    let signal!: AbortSignal;
    vi.stubGlobal(
      "fetch",
      vi.fn((_url, options) => {
        signal = options.signal;
        return new Promise((done) => {
          resolve = done;
        });
      }),
    );
    const load = vi.fn();
    const view = render(
      <AgentDraftImport
        projectId="p1"
        sourceReference={result.source}
        onLoad={load}
        disabled={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Preview AA task" }));
    view.rerender(
      <AgentDraftImport
        projectId={change === "project" ? "p2" : "p1"}
        sourceReference={
          change === "cleared"
            ? null
            : change === "source"
              ? { ...result.source, task_id: "task2" }
              : result.source
        }
        onLoad={load}
        disabled={change === "locked"}
      />,
    );
    expect(signal.aborted).toBe(true);
    resolve({ ok: true, json: async () => result });
    await waitFor(() =>
      expect(screen.queryByText("Loading AA source…")).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "Use as unsaved draft" }),
    ).not.toBeInTheDocument();
    expect(load).not.toHaveBeenCalled();
  },
);
it("invalidates an already reviewed source when the controlled reference changes", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => result }),
  );
  const load = vi.fn();
  const view = render(
    <AgentDraftImport
      projectId="p1"
      sourceReference={result.source}
      onLoad={load}
      disabled={false}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Preview AA task" }));
  await screen.findByRole("button", { name: "Use as unsaved draft" });
  view.rerender(
    <AgentDraftImport
      projectId="p1"
      sourceReference={{ ...result.source, execution_id: "run2" }}
      onLoad={load}
      disabled={false}
    />,
  );
  expect(
    screen.queryByRole("button", { name: "Use as unsaved draft" }),
  ).not.toBeInTheDocument();
  expect(load).not.toHaveBeenCalled();
});
it("retains manual AA task review after a controlled source is cleared", async () => {
  const clear = vi.fn();
  const view = render(
    <AgentDraftImport
      projectId="p1"
      sourceReference={result.source}
      onSourceClear={clear}
      onLoad={vi.fn()}
      disabled={false}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Clear Tapper source" }));
  expect(clear).toHaveBeenCalledOnce();
  view.rerender(
    <AgentDraftImport
      projectId="p1"
      sourceReference={null}
      onSourceClear={clear}
      onLoad={vi.fn()}
      disabled={false}
    />,
  );
  fireEvent.change(screen.getByLabelText("AA task ID"), {
    target: { value: "manual-task" },
  });
  expect(screen.getByLabelText("AA task ID")).toHaveValue("manual-task");
  expect(screen.getByRole("button", { name: "Preview AA task" })).toBeEnabled();
});
it("does not fetch a controlled source while the editor is locked", () => {
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  render(
    <AgentDraftImport
      projectId="p1"
      sourceReference={result.source}
      onLoad={vi.fn()}
      disabled
    />,
  );
  expect(
    screen.getByRole("button", { name: "Preview AA task" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Preview AA task" }));
  expect(fetcher).not.toHaveBeenCalled();
});
