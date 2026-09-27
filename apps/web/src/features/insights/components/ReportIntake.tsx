import { useEffect, useRef, useState } from "react";

import type {
  InsightsDataAdapter,
  ReportManifest,
  ReportReceipt,
} from "../api/client";

const terminal = new Set(["ready", "rejected", "conflicted", "failed"]);
export type ReceiptEventSource = "restored" | "updated";

function requestErrorCopy(cause: unknown) {
  const status = (cause as { status?: number }).status;
  if (status === 401) return "Sign in to manage project reports.";
  if (status === 403) return "You do not have access to this project's reports.";
  if (status === 404) return "The saved report receipt no longer exists.";
  if (status && status >= 500) return "Report intake is temporarily unavailable.";
  return cause instanceof Error ? cause.message : "Report intake is temporarily unavailable.";
}

function failureCopy(receipt: ReportReceipt) {
  const reason = receipt.failureReason ?? "";
  if (reason.includes("mapping") || reason.includes("stable-test"))
    return "Stable test mapping is missing.";
  if (receipt.state === "rejected" || reason.includes("invalid"))
    return "The report format is invalid.";
  if (receipt.state === "conflicted")
    return "This report conflicts with an existing receipt for the same identity.";
  return "The stored report could not be processed.";
}

export function ReportIntake({
  adapter,
  projectId,
  knownReceiptIds = [],
  onReceipt,
}: {
  adapter: InsightsDataAdapter;
  projectId: string;
  knownReceiptIds?: string[];
  onReceipt(receipt: ReportReceipt, source: ReceiptEventSource): void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [receipt, setReceipt] = useState<ReportReceipt | null>(null);
  const [error, setError] = useState("");
  const [duplicate, setDuplicate] = useState(false);
  const [uploading, setUploading] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  const restoredReceipt = useRef(false);

  useEffect(() => {
    const receiptId = knownReceiptIds.at(-1);
    if (!receiptId || restoredReceipt.current) return;
    restoredReceipt.current = true;
    void adapter.getReceipt(projectId, receiptId).then((next) => {
      setReceipt(next);
      onReceipt(next, "restored");
    }).catch((cause: unknown) => {
      setError(requestErrorCopy(cause));
    });
  }, [adapter, knownReceiptIds, onReceipt, projectId]);

  useEffect(() => {
    if (!receipt || terminal.has(receipt.state)) return;
    const timer = window.setTimeout(async () => {
      try {
        const next = await adapter.getReceipt(projectId, receipt.receiptId);
        setReceipt(next);
        onReceipt(next, "updated");
      } catch (cause) {
        setError(requestErrorCopy(cause));
      }
    }, 750);
    return () => window.clearTimeout(timer);
  }, [adapter, onReceipt, projectId, receipt]);

  const submit = async () => {
    if (!file || !form.current) return;
    const values = new FormData(form.current);
    const text = (name: string) => String(values.get(name) ?? "").trim();
    const required = [
      "sourceId",
      "externalRunId",
      "batchId",
      "shardId",
      "applicationCommit",
      "scriptCommit",
      "environment",
      "configuration",
      "startedAt",
    ];
    if (required.some((name) => !text(name))) {
      setError("Complete the report identity before uploading.");
      return;
    }
    const manifest: ReportManifest = {
      projectId,
      sourceId: text("sourceId"),
      externalRunId: text("externalRunId"),
      batchId: text("batchId"),
      shardId: text("shardId"),
      expectedShards: Number(text("expectedShards")),
      containsCompleteAttempts: values.get("containsCompleteAttempts") === "on",
      applicationCommit: text("applicationCommit"),
      scriptCommit: text("scriptCommit"),
      environment: text("environment"),
      configuration: text("configuration"),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      correctionNo: Number(text("correctionNo") || "0"),
      buildId: text("buildId") || undefined,
      branch: text("branch") || undefined,
      startedAt: new Date(text("startedAt")).toISOString(),
    };
    setUploading(true);
    setError("");
    try {
      const next = await adapter.uploadReport(projectId, manifest, file);
      setDuplicate(knownReceiptIds.includes(next.receiptId));
      setReceipt(next);
      onReceipt(next, "updated");
    } catch (cause) {
      setError(requestErrorCopy(cause));
    } finally {
      setUploading(false);
    }
  };

  const retry = async () => {
    if (!receipt) return;
    try {
      const next = await adapter.retryReceipt(projectId, receipt.receiptId);
      setReceipt(next);
      onReceipt(next, "updated");
      setError("");
    } catch (cause) {
      setError(requestErrorCopy(cause));
    }
  };

  return (
    <details className="ti-intake" open={receipt !== null || error !== "" || undefined}>
      <summary>Upload JUnit report</summary>
      <form ref={form} onSubmit={(event) => event.preventDefault()}>
        <div className="ti-intake-grid">
          <label>Source<input name="sourceId" /></label>
          <label>Run<input name="externalRunId" /></label>
          <label>Report build<input name="buildId" /></label>
          <label>Report branch<input name="branch" /></label>
          <label>Batch<input name="batchId" /></label>
          <label>Shard<input name="shardId" /></label>
          <label>Expected shards<input name="expectedShards" type="number" min="1" defaultValue="1" /></label>
          <label>Application commit<input name="applicationCommit" /></label>
          <label>Script commit<input name="scriptCommit" /></label>
          <label>Report environment<input name="environment" /></label>
          <label>Configuration<input name="configuration" /></label>
          <label>Started at<input name="startedAt" type="datetime-local" /></label>
          <label>Correction<input name="correctionNo" type="number" min="0" defaultValue="0" /></label>
          <label className="ti-check"><input name="containsCompleteAttempts" type="checkbox" defaultChecked /> Complete attempt history</label>
          <label className="ti-file">JUnit XML report<input accept=".xml,application/xml,text/xml" type="file" onChange={(event) => {
            const next = event.target.files?.[0] ?? null;
            if (next && !next.name.toLowerCase().endsWith(".xml")) {
              setFile(null);
              setError("Choose a JUnit XML file.");
              return;
            }
            setFile(next);
            setError("");
          }} /></label>
        </div>
        <button className="ti-primary" disabled={!file || uploading} type="button" onClick={submit}>
          {uploading ? "Uploading…" : "Upload report"}
        </button>
      </form>
      {duplicate ? <p role="status">Existing receipt reused; no duplicate facts were created.</p> : null}
      {receipt ? (
        <div className="ti-receipt" aria-live="polite">
          <strong>{receipt.state === "ready" ? "Ready for Insights" : `Report ${receipt.state}`}</strong>
          <span>Receipt {receipt.receiptId}</span>
          <span>Report completeness: {receipt.completeness}</span>
          {receipt.completeness !== "complete" ? <small>First/retry metrics may be unavailable; missing history is never inferred.</small> : null}
          {receipt.state === "failed" ? <button type="button" onClick={retry}>Retry processing stored report</button> : null}
        </div>
      ) : null}
      {receipt && ["failed", "rejected", "conflicted"].includes(receipt.state) ? <p role="alert">{failureCopy(receipt)}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
    </details>
  );
}
