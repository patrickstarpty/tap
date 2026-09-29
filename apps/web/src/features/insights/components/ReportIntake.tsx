import { useEffect, useRef, useState } from "react";

import type {
  InsightsDataAdapter,
  ReportManifest,
  ReportReceipt,
} from "../api/client";

const terminal = new Set(["ready", "rejected", "conflicted", "failed"]);
const attention = new Set(["rejected", "conflicted", "failed"]);
const fileLabel = {
  pytest: "pytest XML report",
  allure: "Allure Results ZIP",
  junit: "JUnit XML report",
} as const;
const fileError = {
  pytest: "Choose a pytest XML file.",
  allure: "Choose an Allure Results ZIP file.",
  junit: "Choose a JUnit XML file.",
} as const;
export type ReceiptEventSource = "restored" | "updated";

function requestErrorCopy(cause: unknown) {
  const status = (cause as { status?: number }).status;
  if (status === 401) return "Sign in to manage project reports.";
  if (status === 403) return "You do not have access to this project's reports.";
  if (status === 404) return "The saved report receipt no longer exists.";
  if (status === 415) return "The file does not match the selected report format.";
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
  const [reportFormat, setReportFormat] = useState<"pytest" | "allure" | "junit">("pytest");
  const [historyComplete, setHistoryComplete] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [receipt, setReceipt] = useState<ReportReceipt | null>(null);
  const [error, setError] = useState("");
  const [duplicate, setDuplicate] = useState(false);
  const [uploading, setUploading] = useState(false);
  const form = useRef<HTMLFormElement>(null);
  const panel = useRef<HTMLDetailsElement>(null);
  const needsAttention = error !== "" || (receipt !== null && attention.has(receipt.state));
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
    // Rejection reasons, request errors and retry live inside the panel.
    if (needsAttention && panel.current) panel.current.open = true;
  }, [needsAttention]);

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
      reportFormat,
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
    <details className="ti-intake" ref={panel}>
      <summary><span>Upload test report</span>{receipt ? <small className={`ti-intake-state ti-intake-state--${receipt.state}`}>{receipt.state === "ready" ? "Ready for Insights" : `Report ${receipt.state}`}</small> : null}</summary>
      <form ref={form} onSubmit={(event) => event.preventDefault()}>
        <div className="ti-intake-grid">
          <label>Report format<select aria-label="Report format" value={reportFormat} onChange={(event) => { setReportFormat(event.target.value as typeof reportFormat); setFile(null); setHistoryComplete(false); setError(""); }}><option value="pytest">pytest JUnit XML</option><option value="allure">Allure Results ZIP</option><option value="junit">JUnit XML (explicit identities)</option></select></label>
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
          <label className="ti-check"><input name="containsCompleteAttempts" type="checkbox" checked={historyComplete} onChange={(event) => setHistoryComplete(event.target.checked)} /> Complete attempt history</label>
          <small>Confirm complete history only when this report includes every attempt from the run. Missing history keeps first-pass and retry metrics unavailable.</small>
          <label className="ti-file">{fileLabel[reportFormat]}<input key={reportFormat} accept={reportFormat === "allure" ? ".zip,application/zip" : ".xml,application/xml,text/xml"} type="file" onChange={(event) => {
            setHistoryComplete(false);
            const next = event.target.files?.[0] ?? null;
            if (next && !next.name.toLowerCase().endsWith(reportFormat === "allure" ? ".zip" : ".xml")) {
              setFile(null);
              setError(fileError[reportFormat]);
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
          <strong>Processing receipt</strong>
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
