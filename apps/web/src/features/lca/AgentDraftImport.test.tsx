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
