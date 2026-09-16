import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { createInitialArtifactState } from "./legacy/artifacts/fixtures";
import { createSimulatedRun } from "./legacy/artifacts/state";

function legacyWorkspace() {
  const state = createInitialArtifactState();
  const automation = {
    ...state.automations[0]!,
    title: "Recovered underwriting edits",
    revision: 9,
  };
  const run = createSimulatedRun({
    automation,
    id: "RUN-before-split",
    target: {
      kind: "web",
      executionAgentId: "ado-web-agent-03",
      label: "ADO Web Agent 03",
    },
    triggeredFrom: "automation",
    timestamp: "2026-09-14T00:00:00Z",
  });
  return JSON.stringify({
    version: 2,
    artifacts: { ...state, automations: [automation], runs: [run] },
  });
}

beforeEach(() => localStorage.clear());
afterEach(cleanup);
afterEach(() => vi.restoreAllMocks());

describe("TAP non-AI application", () => {
  it("lets the user recover legacy data even after a new workspace was already saved", async () => {
    const user = userEvent.setup();
    localStorage.setItem("tap.prototype.workspace.v2", legacyWorkspace());
    localStorage.setItem(
      "tap.automation.workspace.v1",
      JSON.stringify({ version: 1, state: createInitialArtifactState() }),
    );
    render(<App />);
    expect(
      screen.getByText("Life insurance application automation"),
    ).toBeVisible();
    await user.click(screen.getByText("Local workspace"));
    await user.click(
      screen.getByRole("button", { name: "Review pre-split workspace" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Restore imported workspace" }),
    );
    expect(screen.getByText("Recovered underwriting edits")).toBeVisible();
    expect(localStorage.getItem("tap.prototype.workspace.v2")).toBe(
      legacyWorkspace(),
    );
  });

  it("exports legacy edits and runs and restores them in a fresh origin with a backup", async () => {
    const user = userEvent.setup();
    const legacy = legacyWorkspace();
    localStorage.setItem("tap.prototype.workspace.v2", legacy);
    const oldOrigin = render(<App />);
    expect(screen.getByText("Recovered underwriting edits")).toBeVisible();
    await user.click(screen.getByText("Local workspace"));
    const exportLink = screen.getByRole("link", { name: "Export workspace" });
    const exported = decodeURIComponent(
      exportLink.getAttribute("href")!.split(",")[1]!,
    );
    expect(JSON.parse(exported).state.runs[0].id).toBe("RUN-before-split");
    expect(localStorage.getItem("tap.prototype.workspace.v2")).toBe(legacy);
    oldOrigin.unmount();
    localStorage.clear(); // A different origin has its own empty storage.

    const newOrigin = render(<App />);
    await user.click(screen.getByText("Local workspace"));
    await user.upload(
      screen.getByLabelText("Import workspace"),
      new File([exported], "tap-local-workspace.json", {
        type: "application/json",
      }),
    );
    expect(
      screen.getByText("Life insurance application automation"),
    ).toBeVisible();
    await user.click(
      await screen.findByRole("button", { name: "Restore imported workspace" }),
    );
    expect(screen.getByText("Recovered underwriting edits")).toBeVisible();
    const backups = Object.keys(localStorage).filter((key) =>
      key.startsWith("tap.automation.workspace.backup."),
    );
    expect(backups).toHaveLength(1);
    expect(
      JSON.parse(localStorage.getItem(backups[0]!)!).state.automations[0].title,
    ).toBe("Life insurance application automation");
    const backupLink = screen.getByRole("link", {
      name: "Export previous workspace",
    });
    expect(
      JSON.parse(
        decodeURIComponent(backupLink.getAttribute("href")!.split(",")[1]!),
      ).state.automations,
    ).toHaveLength(2);
    newOrigin.unmount();
    render(<App />);
    await user.click(screen.getByText("Local workspace"));
    expect(
      screen.getByRole("link", { name: "Export previous workspace" }),
    ).toBeVisible();
    await user.click(screen.getByText("Recovered underwriting edits"));
    expect(
      within(
        screen.getByRole("region", { name: "Automation run history" }),
      ).getByText("RUN-before-split"),
    ).toBeVisible();
  });

  it.each([
    "not JSON",
    JSON.stringify({
      version: 2,
      artifacts: { automations: [null], testPlans: [], runs: [] },
    }),
  ])(
    "rejects malformed imports without replacing the workspace: %s",
    async (contents) => {
      const user = userEvent.setup();
      render(<App />);
      const before = localStorage.getItem("tap.automation.workspace.v1");
      await user.click(screen.getByText("Local workspace"));
      await user.upload(
        screen.getByLabelText("Import workspace"),
        new File([contents], "broken.json", { type: "application/json" }),
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "File is not a valid TAP local workspace.",
      );
      expect(
        screen.queryByRole("button", { name: "Restore imported workspace" }),
      ).not.toBeInTheDocument();
      expect(localStorage.getItem("tap.automation.workspace.v1")).toBe(before);
    },
  );

  it.each(["backup", "replacement"])(
    "does not replace the workspace if saving its %s fails",
    async (failure) => {
      const user = userEvent.setup();
      render(<App />);
      await user.click(screen.getByText("Local workspace"));
      await user.upload(
        screen.getByLabelText("Import workspace"),
        new File([legacyWorkspace()], "legacy.json", {
          type: "application/json",
        }),
      );
      const restore = await screen.findByRole("button", {
        name: "Restore imported workspace",
      });
      const originalSet = Storage.prototype.setItem;
      vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (
        this: Storage,
        key,
        value,
      ) {
        if (failure === "backup" || key === "tap.automation.workspace.v1")
          throw new Error("quota");
        originalSet.call(this, key, value);
      });
      await user.click(restore);
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Could not save the workspace and its backup. Nothing was replaced.",
      );
      expect(
        screen.getByText("Life insurance application automation"),
      ).toBeVisible();
      expect(
        screen.queryByText("Recovered underwriting edits"),
      ).not.toBeInTheDocument();
    },
  );

  it("restores a created automation, edited BDD step and simulated run after remount", async () => {
    const user = userEvent.setup();
    const mounted = render(<App />);
    await user.click(screen.getByRole("button", { name: "New automation" }));
    await user.type(
      screen.getByRole("textbox", { name: "Automation title" }),
      "Persistent browser journey",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Automation type" }),
      "web",
    );
    await user.click(
      screen.getByRole("button", { name: "Create blank automation" }),
    );
    const step = screen.getByRole("textbox", { name: "BDD step text 1" });
    await user.clear(step);
    await user.type(step, "the saved customer opens the application");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Execution Agent" }),
      "ado-web-agent-03",
    );
    await user.click(screen.getByRole("button", { name: "Run automation" }));
    expect(
      within(
        screen.getByRole("region", { name: "Automation run history" }),
      ).queryByText("No simulated runs yet"),
    ).not.toBeInTheDocument();
    mounted.unmount();
    render(<App />);
    await user.click(screen.getByText("Persistent browser journey"));
    expect(
      screen.getByRole("textbox", { name: "BDD step text 1" }),
    ).toHaveValue("the saved customer opens the application");
    expect(
      within(
        screen.getByRole("region", { name: "Automation run history" }),
      ).queryByText("No simulated runs yet"),
    ).not.toBeInTheDocument();
  });

  it("opens automation and analytics without mounting Tapper", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(
      screen.getByRole("button", { name: "Low Code Automation" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Tapper" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Test Analytics" }));
    expect(
      screen.getByRole("heading", { name: "Demo Dashboard" }),
    ).toBeVisible();
  });

  it("keeps the automation editor free of the Tapper agent panel", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByText("Life insurance application automation"));
    expect(
      screen.queryByRole("tab", { name: "AI Agent" }),
    ).not.toBeInTheDocument();
  });
});
