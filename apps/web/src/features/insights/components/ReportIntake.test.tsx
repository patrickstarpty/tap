import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import type { InsightsDataAdapter, ReportReceipt } from "../api/client";
import { ReportIntake } from "./ReportIntake";

const received: ReportReceipt = {
  receiptId: "receipt-1",
  projectId: "project-a",
  sourceId: "github-actions",
  externalRunId: "RUN-1042",
  batchId: "batch-1",
  shardId: "1",
  checksum: "abc",
  parserVersion: "junit-v1",
  correctionNo: 0,
  state: "received",
  completeness: "complete",
  sizeBytes: 128,
  conflictWithReceiptId: null,
  failureReason: null,
  evidenceUrl: "/evidence/receipt-1",
};

function intakeAdapter(): InsightsDataAdapter {
  return {
    listMetrics: vi.fn(),
    createQuery: vi.fn(),
    getQuery: vi.fn(),
    listRuns: vi.fn(),
    listFailures: vi.fn(),
    listAttempts: vi.fn(),
    uploadReport: vi.fn().mockResolvedValue(received),
    getReceipt: vi.fn()
      .mockResolvedValueOnce({ ...received, state: "validating" })
      .mockResolvedValue({ ...received, state: "ready" }),
    retryReceipt: vi.fn().mockResolvedValue({ ...received, state: "validating" }),
    downloadEvidence: vi.fn(),
    exportQuery: vi.fn(),
  };
}

async function completeIdentity(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Source"), "github-actions");
  await user.type(screen.getByLabelText("Run"), "RUN-1042");
  await user.type(screen.getByLabelText("Report build"), "BUILD-1042");
  await user.type(screen.getByLabelText("Report branch"), "main");
  await user.type(screen.getByLabelText("Batch"), "batch-1");
  await user.type(screen.getByLabelText("Shard"), "1");
  await user.type(screen.getByLabelText("Application commit"), "app-1");
  await user.type(screen.getByLabelText("Script commit"), "script-1");
  await user.type(screen.getByLabelText("Report environment"), "qa");
  await user.type(screen.getByLabelText("Configuration"), "browser-chromium");
  fireEvent.change(screen.getByLabelText("Started at"), {
    target: { value: "2026-09-24T08:00" },
  });
}

it("uploads JUnit, polls the receipt to ready and reports an exact duplicate", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const adapter = intakeAdapter();
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(
    <ReportIntake
      adapter={adapter}
      projectId="project-a"
      knownReceiptIds={["receipt-1"]}
      onReceipt={vi.fn()}
    />,
  );
  await completeIdentity(user);
  await user.upload(
    screen.getByLabelText("JUnit XML report"),
    new File(["<testsuite/>"] , "report.xml", { type: "application/xml" }),
  );
  await user.click(screen.getByRole("button", { name: "Upload report" }));
  expect(await screen.findByText("Existing receipt reused; no duplicate facts were created.")).toBeVisible();
  await vi.advanceTimersByTimeAsync(2_000);
  expect(await screen.findByText("Ready for Insights")).toBeVisible();
  expect(adapter.getReceipt).toHaveBeenCalledWith("project-a", "receipt-1");
  vi.useRealTimers();
});

it("restores the latest receipt from the server after a page reload", async () => {
  const adapter = intakeAdapter();
  adapter.getReceipt = vi.fn().mockResolvedValue({ ...received, state: "ready" });

  render(
    <ReportIntake
      adapter={adapter}
      projectId="project-a"
      knownReceiptIds={["receipt-1"]}
      onReceipt={vi.fn()}
    />,
  );

  expect(await screen.findByText("Ready for Insights")).toBeVisible();
  expect(adapter.getReceipt).toHaveBeenCalledWith("project-a", "receipt-1");
});

it.each([
  ["mapping-missing-stable-test-id", "Stable test mapping is missing."],
  ["invalid-junit-xml", "The report format is invalid."],
])("shows actionable receipt failure %s", async (failureReason, expected) => {
  const adapter = intakeAdapter();
  adapter.getReceipt = vi.fn().mockResolvedValue({
    ...received,
    state: failureReason.startsWith("invalid") ? "rejected" : "failed",
    failureReason,
    completeness: "partial",
  });
  const user = userEvent.setup();
  render(<ReportIntake adapter={adapter} projectId="project-a" onReceipt={vi.fn()} />);
  await completeIdentity(user);
  await user.upload(screen.getByLabelText("JUnit XML report"), new File(["bad"], "report.xml"));
  await user.click(screen.getByRole("button", { name: "Upload report" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(expected);
  expect(screen.getByText("Report completeness: partial")).toBeVisible();
});

it("retries server intake only and never claims to rerun tests", async () => {
  const adapter = intakeAdapter();
  adapter.getReceipt = vi.fn().mockResolvedValue({
    ...received,
    state: "failed",
    failureReason: "mapping-failed",
  });
  const user = userEvent.setup();
  render(<ReportIntake adapter={adapter} projectId="project-a" onReceipt={vi.fn()} />);
  await completeIdentity(user);
  await user.upload(screen.getByLabelText("JUnit XML report"), new File(["bad"], "report.xml"));
  await user.click(screen.getByRole("button", { name: "Upload report" }));
  await user.click(await screen.findByRole("button", { name: "Retry processing stored report" }));
  await waitFor(() => expect(adapter.retryReceipt).toHaveBeenCalledWith("project-a", "receipt-1"));
  expect(screen.queryByText(/rerun tests/i)).not.toBeInTheDocument();
});

it("rejects non-XML files before making a request", async () => {
  const adapter = intakeAdapter();
  const user = userEvent.setup();
  render(<ReportIntake adapter={adapter} projectId="project-a" onReceipt={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("JUnit XML report"), {
    target: { files: [new File(["{}"], "report.json")] },
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("Choose a JUnit XML file.");
  expect(adapter.uploadReport).not.toHaveBeenCalled();
});
