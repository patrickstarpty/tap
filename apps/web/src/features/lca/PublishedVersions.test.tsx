import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { PublishedVersions } from "./PublishedVersions";
import { createLcaClient } from "./client";
it("publishes only an explicitly selected passing version and keeps execution separate", async () => {
  const api = {
    ...createLcaClient(),
    listReleases: vi.fn().mockResolvedValue([]),
    publish: vi.fn().mockResolvedValue({
      id: "release-1",
      version: 2,
      created_at: "2026-11-11",
      definition_digest: "hash",
      verified_run_id: "run-1",
    }),
    createPublishedRun: vi.fn(),
  };
  const props = {
    client: api,
    projectId: "p",
    flowId: "f",
    version: 2,
    verifiedRunId: "run-1",
    locked: false,
    dirty: false,
    onRun: vi.fn(),
  };
  render(<PublishedVersions {...props} />);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Publish verified version" }),
  );
  await waitFor(() =>
    expect(api.publish).toHaveBeenCalledWith(
      "p",
      "f",
      { expected_version: 2, verified_run_id: "run-1" },
      expect.any(String),
    ),
  );
  expect(api.createPublishedRun).not.toHaveBeenCalled();
  expect(await screen.findByText(/Version 2/)).toBeVisible();
});
it("never publishes unverified or unsaved changes", async () => {
  const api = {
    ...createLcaClient(),
    listReleases: vi.fn().mockResolvedValue([]),
  };
  render(
    <PublishedVersions
      client={api}
      projectId="p"
      flowId="f"
      version={2}
      dirty
      locked={false}
      onRun={() => {}}
    />,
  );
  expect(
    screen.getByRole("button", { name: "Publish verified version" }),
  ).toBeDisabled();
});
it("loads the immutable published parameter schema before execution", async () => {
  const release = {
    id: "r",
    flow_id: "f",
    version: 1,
    created_at: "today",
    definition_digest: "hash",
    verified_run_id: "run-1",
  };
  const api = {
    ...createLcaClient(),
    listReleases: vi.fn().mockResolvedValue([release]),
    getRelease: vi.fn().mockResolvedValue({
      ...release,
      definition: {
        schema_version: "lca-debug-v1",
        allowed_origins: [],
        targets: [],
        steps: [],
        parameters: [{ name: "age", type: "integer", required: true }],
      },
    }),
    createPublishedRun: vi.fn().mockResolvedValue({ id: "published-run" }),
  };
  const onRun = vi.fn();
  const user = userEvent.setup();
  render(
    <PublishedVersions
      client={api}
      projectId="p"
      flowId="f"
      version={7}
      dirty={false}
      locked={false}
      onRun={onRun}
    />,
  );
  await user.click(
    await screen.findByRole("button", { name: "Run published version 1" }),
  );
  expect(api.createPublishedRun).not.toHaveBeenCalled();
  await user.type(
    await screen.findByLabelText("Published age (integer)"),
    "30",
  );
  await user.click(
    screen.getByRole("button", { name: "Confirm published replay" }),
  );
  await waitFor(() =>
    expect(api.createPublishedRun).toHaveBeenCalledWith(
      "p",
      "r",
      { parameters: { age: 30 } },
      expect.any(String),
    ),
  );
  expect(onRun).toHaveBeenCalledWith({ id: "published-run" });
});

it("admits only one published-version review while its response is pending", async () => {
  const release = {
    id: "r",
    flow_id: "f",
    version: 1,
    created_at: "today",
    definition_digest: "hash",
    verified_run_id: "run-1",
  };
  const api = {
    ...createLcaClient(),
    listReleases: vi.fn().mockResolvedValue([release]),
    getRelease: vi.fn(() => new Promise<never>(() => {})),
  };
  render(
    <PublishedVersions
      client={api}
      projectId="p"
      flowId="f"
      version={1}
      dirty={false}
      locked={false}
      onRun={() => {}}
    />,
  );
  const button = await screen.findByRole("button", {
    name: "Run published version 1",
  });
  const { act } = await import("@testing-library/react");
  act(() => {
    button.click();
    button.click();
  });
  expect(api.getRelease).toHaveBeenCalledTimes(1);
});

it("retains an uncertain publication key after a rejected retry", async () => {
  const { LcaHttpError } = await import("./client");
  const api = {
    ...createLcaClient(),
    listReleases: vi.fn().mockResolvedValue([]),
    publish: vi
      .fn()
      .mockRejectedValueOnce(new LcaHttpError(502))
      .mockRejectedValueOnce(new LcaHttpError(403))
      .mockResolvedValue({
        id: "r",
        version: 1,
        created_at: "today",
        verified_run_id: "run-1",
      }),
  };
  const active = vi.fn();
  const user = userEvent.setup();
  render(
    <PublishedVersions
      client={api}
      projectId="p"
      flowId="f"
      version={1}
      verifiedRunId="run-1"
      dirty={false}
      locked={false}
      onRun={() => {}}
      onActiveChange={active}
    />,
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Publish verified version" }),
    ).toBeEnabled(),
  );
  await user.click(
    screen.getByRole("button", { name: "Publish verified version" }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Retry same publish request" }),
  );
  expect(
    await screen.findByRole("button", { name: "Retry same publish request" }),
  ).toBeVisible();
  expect(active).not.toHaveBeenCalledWith(false);
  await user.click(
    screen.getByRole("button", { name: "Retry same publish request" }),
  );
  await waitFor(() => expect(active).toHaveBeenCalledWith(false));
  expect(new Set(api.publish.mock.calls.map((call) => call[3])).size).toBe(1);
});

it("can retry loading published versions without discarding the draft", async () => {
  const api = {
    ...createLcaClient(),
    listReleases: vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce([]),
  };
  const user = userEvent.setup();
  render(
    <PublishedVersions
      client={api}
      projectId="p"
      flowId="f"
      version={1}
      dirty={false}
      locked={false}
      onRun={() => {}}
    />,
  );
  await screen.findByRole("alert");
  await user.click(
    screen.getByRole("button", { name: "Refresh published versions" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
  );
  expect(api.listReleases).toHaveBeenCalledTimes(2);
});
