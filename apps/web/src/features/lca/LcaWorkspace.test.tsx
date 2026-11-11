import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { createRecorderClient } from "./recorderClient";
const recorderClient = {
  ...createRecorderClient(),
  list: vi.fn().mockResolvedValue([]),
};
import { LcaWorkspace } from "./LcaWorkspace";
import {
  LcaHttpError,
  type LcaClient,
  type LcaFlow,
  type LcaRun,
} from "./client";

const flow: LcaFlow = {
  id: "flow-1",
  project_id: "p",
  title: "Checkout",
  version: 1,
  definition: {
    schema_version: "lca-debug-v1",
    allowed_origins: ["https://example.com"],
    targets: [],
    parameters: [],
    steps: [
      {
        id: "open",
        operation: "navigate",
        arguments: { url: "https://example.com" },
        timeout_ms: 10000,
      },
    ],
  },
};
const run: LcaRun = {
  id: "run-1",
  flow_id: flow.id,
  snapshot_id: "snap-1",
  definition_digest: "abc",
  input_digest: "xyz",
  bundle_id: "bundle-1",
  execution_id: "exec-1",
  classification: "debug",
};
function client(): LcaClient {
  return {
    listAssets: vi.fn().mockResolvedValue([]),
    uploadAsset: vi.fn(),
    getRelease: vi.fn(),
    listReleases: vi.fn().mockResolvedValue([]),
    publish: vi.fn(),
    createPublishedRun: vi.fn(),
    reconcileRun: vi.fn(),
    cancelRun: vi.fn(),
    getEvidence: vi.fn(),
    getEvidenceDownload: vi.fn(),
    listFlows: vi.fn().mockResolvedValue([flow]),
    getFlow: vi.fn().mockResolvedValue(flow),
    createFlow: vi.fn(),
    saveDraft: vi.fn().mockResolvedValue({ ...flow, version: 2 }),
    listRuns: vi.fn().mockResolvedValue([]),
    createRun: vi.fn().mockResolvedValue(run),
    getRun: vi.fn().mockResolvedValue({
      ...run,
      execution: { status: "unknown", steps: [], evidence: [] },
    }),
  };
}
async function open(api: LcaClient) {
  const user = userEvent.setup();
  render(
    <LcaWorkspace projectId="p" client={api} recorderClient={recorderClient} />,
  );
  await user.click(await screen.findByRole("button", { name: "Checkout" }));
  await screen.findByLabelText("Step open value");
  return user;
}
it("loads persisted steps, saves explicitly and never executes on editing", async () => {
  const api = client();
  const user = await open(api);
  fireEvent.change(screen.getByLabelText("Step open value"), {
    target: { value: "https://example.com/cart" },
  });
  expect(screen.getByRole("button", { name: "Debug replay" })).toBeDisabled();
  expect(api.createRun).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  expect(
    await screen.findByText(
      "Draft saved as version 2. Saving did not start a replay.",
    ),
  ).toBeVisible();
  expect(api.saveDraft).toHaveBeenCalledWith(
    "p",
    "flow-1",
    expect.objectContaining({
      expected_version: 1,
      definition: expect.objectContaining({
        steps: [
          expect.objectContaining({
            arguments: { url: "https://example.com/cart" },
          }),
        ],
      }),
    }),
  );
});
it("preserves edits on conflict and does not silently overwrite", async () => {
  const api = client();
  api.saveDraft = vi.fn().mockRejectedValue(new LcaHttpError(409));
  const user = await open(api);
  fireEvent.change(screen.getByLabelText("Step open value"), {
    target: { value: "https://example.com/new" },
  });
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("changed");
  expect(screen.getByLabelText("Step open value")).toHaveValue(
    "https://example.com/new",
  );
  expect(api.saveDraft).toHaveBeenCalledTimes(1);
});
it("reconciles an uncertain replay using the original frozen request and key", async () => {
  const api = client();
  api.createRun = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("Network lost"))
    .mockResolvedValue(run);
  const user = await open(api);
  await user.click(screen.getByRole("button", { name: "Debug replay" }));
  await user.click(
    await screen.findByRole("button", { name: "Reconcile replay request" }),
  );
  const calls = vi.mocked(api.createRun).mock.calls;
  expect(calls).toHaveLength(2);
  expect(calls[1]).toEqual(calls[0]);
  expect(
    await screen.findByText(
      "Outcome unknown. Do not automatically repeat this run.",
    ),
  ).toBeVisible();
  expect(screen.getByText("No evidence available.")).toBeVisible();
  expect(screen.getByText("snap-1")).toBeVisible();
});
it("shows unavailable gateway without inventing flows or recording", async () => {
  const api = client();
  api.listFlows = vi.fn().mockRejectedValue(new LcaHttpError(503));
  render(
    <LcaWorkspace projectId="p" client={api} recorderClient={recorderClient} />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
  await userEvent.click(
    screen.getByText("Browser recording · append actions to a BDD step"),
  );
  expect(screen.getByText(/Save or open a debug flow/)).toBeVisible();
  expect(api.createRun).not.toHaveBeenCalled();
});
it("adds an explicit assertion and reorders stable step IDs", async () => {
  const api = client();
  const user = await open(api);
  await user.click(screen.getByRole("button", { name: "Add assertion" }));
  expect(screen.getByLabelText(/Step step_.* operation/)).toHaveValue(
    "assert_url",
  );
  await user.click(screen.getByRole("button", { name: /Move step_.* up/ }));
  await user.click(screen.getByRole("button", { name: "Save draft" }));
  await waitFor(() => expect(api.saveDraft).toHaveBeenCalled());
  const steps = vi.mocked(api.saveDraft).mock.calls[0][2].definition.steps;
  expect(steps[0].operation).toBe("assert_url");
  expect(steps[1].id).toBe("open");
});

it("sends cancellation without claiming the execution has stopped", async () => {
  const api = client();
  api.getRun = vi
    .fn()
    .mockResolvedValue({ ...run, execution: { status: "running" } });
  api.cancelRun = vi.fn().mockResolvedValue({ status: "running" });
  const user = await open(api);
  await user.click(screen.getByRole("button", { name: "Debug replay" }));
  await user.click(
    await screen.findByRole("button", { name: "Request cancellation" }),
  );
  expect(api.cancelRun).toHaveBeenCalledWith("p", "run-1");
  expect(
    await screen.findByText(/Cancellation requested. Waiting/),
  ).toBeVisible();
});

it("keeps numeric and boolean replay values typed", async () => {
  const api = client();
  api.getFlow = vi.fn().mockResolvedValue({
    ...flow,
    definition: {
      ...flow.definition,
      parameters: [
        { name: "count", type: "integer", required: true },
        { name: "enabled", type: "boolean", required: true },
      ],
    },
  });
  const user = await open(api);
  fireEvent.change(screen.getByLabelText("count (integer, required)"), {
    target: { value: "3" },
  });
  fireEvent.change(screen.getByLabelText("enabled (boolean, required)"), {
    target: { value: "false" },
  });
  await user.click(screen.getByRole("button", { name: "Debug replay" }));
  expect(api.createRun).toHaveBeenCalledWith(
    "p",
    "flow-1",
    { expected_version: 1, parameters: { count: 3, enabled: false } },
    expect.any(String),
  );
});

it("ignores an old project's delayed flow list", async () => {
  const api = client();
  let resolveOld!: (flows: LcaFlow[]) => void;
  api.listFlows = vi.fn().mockImplementation((project) =>
    project === "p"
      ? new Promise<LcaFlow[]>((resolve) => {
          resolveOld = resolve;
        })
      : Promise.resolve([
          { ...flow, id: "other", project_id: "q", title: "Other project" },
        ]),
  );
  const view = render(
    <LcaWorkspace projectId="p" client={api} recorderClient={recorderClient} />,
  );
  view.rerender(
    <LcaWorkspace projectId="q" client={api} recorderClient={recorderClient} />,
  );
  expect(
    await screen.findByRole("button", { name: "Other project" }),
  ).toBeVisible();
  resolveOld([flow]);
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Checkout" }),
    ).not.toBeInTheDocument(),
  );
});

it("keeps canonical attachment failures separate from raw diagnostic titles", async () => {
  const api = client();
  api.getRun = vi.fn().mockResolvedValue({
    ...run,
    canonical_results: null,
    canonical_results_error: "Snapshot digest mismatch",
    execution: {
      status: "failed",
      steps: [{ title: "lca:open", status: "passed" }],
      evidence: [],
    },
  });
  const user = await open(api);
  await user.click(screen.getByRole("button", { name: "Debug replay" }));
  expect(
    await screen.findByText(
      /Canonical results unavailable.*Snapshot digest mismatch/,
    ),
  ).toBeVisible();
  expect(
    screen.getByText("Runtime diagnostics (not canonical step results)"),
  ).toBeVisible();
  expect(screen.getByText("lca:open")).toBeVisible();
});

it("fences repeated clicks while replay admission is in flight", async () => {
  const api = client();
  let resolveRun!: (run: LcaRun) => void;
  api.createRun = vi.fn().mockImplementation(
    () =>
      new Promise<LcaRun>((resolve) => {
        resolveRun = resolve;
      }),
  );
  await open(api);
  const button = screen.getByRole("button", { name: "Debug replay" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  fireEvent.click(button);
  expect(api.createRun).toHaveBeenCalledTimes(1);
  resolveRun(run);
  expect(
    await screen.findByText(
      "Outcome unknown. Do not automatically repeat this run.",
    ),
  ).toBeVisible();
});

it("recovers an unlinked durable intent after reload without a new replay", async () => {
  const api = client();
  api.listRuns = vi.fn().mockResolvedValue([{ ...run, execution_id: null }]);
  api.reconcileRun = vi.fn().mockResolvedValue(run);
  const user = await open(api);
  expect(screen.getByRole("button", { name: "Debug replay" })).toBeDisabled();
  expect(api.reconcileRun).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Reconcile existing run run-1" }),
  );
  expect(api.reconcileRun).toHaveBeenCalledWith("p", "run-1");
  expect(api.createRun).not.toHaveBeenCalled();
});

it("clears old history immediately while switching flow and ignores late receipt", async () => {
  const api = client();
  const other = { ...flow, id: "flow-2", title: "Second" };
  api.listFlows = vi.fn().mockResolvedValue([flow, other]);
  api.listRuns = vi.fn().mockResolvedValueOnce([run]).mockResolvedValue([]);
  let resolveReceipt!: (value: LcaRun) => void;
  api.getRun = vi.fn().mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveReceipt = resolve;
      }),
  );
  let resolveFlow!: (value: LcaFlow) => void;
  api.getFlow = vi
    .fn()
    .mockResolvedValueOnce(flow)
    .mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFlow = resolve;
        }),
    );
  const user = await open(api);
  await user.click(screen.getByRole("button", { name: "run-1" }));
  await user.click(screen.getByRole("button", { name: "Second" }));
  expect(
    screen.queryByRole("button", { name: "run-1" }),
  ).not.toBeInTheDocument();
  resolveReceipt({ ...run, execution: { status: "passed" } });
  resolveFlow(other);
  await waitFor(() =>
    expect(screen.getByLabelText("Flow title")).toHaveValue("Second"),
  );
  expect(
    screen.queryByRole("article", { name: "Run run-1" }),
  ).not.toBeInTheDocument();
});

it("retains explicit refresh after transient polling failure without repeated requests", async () => {
  const api = client();
  api.getRun = vi
    .fn()
    .mockResolvedValueOnce({ ...run, execution: { status: "running" } })
    .mockRejectedValueOnce(new TypeError("Offline"))
    .mockResolvedValue({ ...run, execution: { status: "passed" } });
  const user = await open(api);
  await user.click(screen.getByRole("button", { name: "Debug replay" }));
  await screen.findByRole("button", { name: "Request cancellation" });
  await waitFor(
    () =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Automatic receipt checks paused",
      ),
    { timeout: 2500 },
  );
  expect(api.getRun).toHaveBeenCalledTimes(2);
  await user.click(screen.getByRole("button", { name: "Refresh run receipt" }));
  expect(await screen.findByText("passed")).toBeVisible();
  expect(api.createRun).toHaveBeenCalledTimes(1);
});
it("shows bounded plain-text diagnostics without ANSI or control sequences", async () => {
  const api = client();
  api.getRun = vi.fn().mockResolvedValue({
    ...run,
    execution: {
      status: "failed",
      steps: [
        {
          title: "Assertion",
          status: "failed",
          error: "\u001b[31mExpected\u001b[0m\u0007 " + "x".repeat(1200),
        },
      ],
    },
  });
  const user = await open(api);
  await user.click(screen.getByRole("button", { name: "Debug replay" }));
  const diagnostic = await screen.findByTestId("lca-diagnostic");
  expect(diagnostic.textContent).toBe("Expected " + "x".repeat(991));
  expect(diagnostic).toHaveClass("lca-diagnostic");
});
it("keeps an empty BDD business step editable but blocks incomplete replay", async () => {
  const api = client();
  api.getFlow = vi.fn().mockResolvedValue({
    ...flow,
    definition: {
      ...flow.definition,
      bdd_steps: [
        {
          id: "given",
          keyword: "Given",
          description: "Open",
          action_ids: ["open"],
        },
        {
          id: "then",
          keyword: "Then",
          description: "Verify quotation",
          action_ids: [],
        },
      ],
    },
  });
  await open(api);
  expect(screen.getByRole("button", { name: "Debug replay" })).toBeDisabled();
  expect(
    screen.getByText("Add actions to empty BDD steps before replay."),
  ).toBeVisible();
  expect(api.createRun).not.toHaveBeenCalled();
});

it.each([
  ["invoice.csv", "invoice.csv"],
  [null, "debug-download.bin"],
])(
  "downloads captured files as raw attachments, retaining safe name %s",
  async (filename, expected) => {
    const api = client();
    vi.mocked(api.listRuns).mockResolvedValue([run]);
    vi.mocked(api.getRun).mockResolvedValue({
      ...run,
      execution: {
        status: "passed",
        evidence: [
          {
            kind: "download",
            object_ref: "download-1",
            size_bytes: 5,
            sha256: "a".repeat(64),
          },
        ],
      },
    });
    const bytes = new Blob(["hello"], { type: "application/octet-stream" });
    vi.mocked(api.getEvidenceDownload).mockResolvedValue({
      blob: bytes,
      filename,
    });
    const objectUrl = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:captured-download");
    const revoke = vi
      .spyOn(URL, "revokeObjectURL")
      .mockImplementation(() => {});
    let attachment: { href: string; filename: string } | undefined;
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        attachment = { href: this.href, filename: this.download };
      });
    try {
      const user = await open(api);
      await user.click(screen.getByRole("button", { name: "run-1" }));
      await user.click(
        await screen.findByRole("button", { name: "Download download" }),
      );
      expect(api.getEvidenceDownload).toHaveBeenCalledWith(
        "p",
        "run-1",
        "download-1",
      );
      expect(objectUrl).toHaveBeenCalledWith(bytes);
      expect(attachment).toEqual({
        href: "blob:captured-download",
        filename: expected,
      });
    } finally {
      click.mockRestore();
      objectUrl.mockRestore();
      revoke.mockRestore();
    }
  },
);

it.each(["switch flow", "reload draft"])(
  "does not bind a late file upload after %s with the same action ID",
  async (action) => {
    const api = client();
    const uploadFlow: LcaFlow = {
      ...flow,
      title: "Upload A",
      definition: {
        ...flow.definition,
        targets: [{ id: "input", kind: "test_id", value: "input" }],
        steps: [
          {
            id: "upload",
            operation: "upload_file",
            target_id: "input",
            arguments: { asset_id: "", sha256: "" },
            timeout_ms: 10000,
          },
        ],
      },
    };
    const other: LcaFlow = { ...uploadFlow, id: "flow-2", title: "Upload B" };
    vi.mocked(api.listFlows).mockResolvedValue([uploadFlow, other]);
    vi.mocked(api.getFlow).mockImplementation(async (_project, id) =>
      id === other.id ? other : uploadFlow,
    );
    let finish!: (asset: Awaited<ReturnType<LcaClient["uploadAsset"]>>) => void;
    vi.mocked(api.uploadAsset).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const user = userEvent.setup();
    render(
      <LcaWorkspace
        projectId="p"
        client={api}
        recorderClient={recorderClient}
      />,
    );
    await user.click(await screen.findByRole("button", { name: "Upload A" }));
    await screen.findByText("No project files loaded.");
    fireEvent.change(screen.getByLabelText("Choose project file"), {
      target: { files: [new File(["hello"], "invoice.csv")] },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Upload selected file" }),
    );
    await waitFor(() => expect(finish).toBeTypeOf("function"));
    if (action === "switch flow") {
      await user.click(screen.getByRole("button", { name: "Upload B" }));
      await waitFor(() =>
        expect(screen.getByLabelText("Flow title")).toHaveValue("Upload B"),
      );
    } else {
      await user.click(
        screen.getByRole("button", { name: "Reload saved draft" }),
      );
      await user.click(
        screen.getByRole("button", { name: "Discard edits and reload" }),
      );
      await waitFor(() => expect(api.getFlow).toHaveBeenCalledTimes(2));
    }
    finish({
      id: "asset-old-flow",
      filename: "invoice.csv",
      sha256: "a".repeat(64),
      content_type: "text/csv",
      size_bytes: 5,
      created_at: "2026-11-11T12:00:00Z",
    });
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Upload selected file" }),
      ).toBeDisabled(),
    );
    expect(screen.getByLabelText("Step upload project file")).toHaveValue("");
    expect(screen.queryByText(/SHA-256:/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
  },
);
