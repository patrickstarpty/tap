import { AttemptEvidence } from "./AttemptEvidence";
import { useRef, useState } from "react";

import type {
  AttemptDetail,
  InsightsDataAdapter,
  ReportEvidence,
  RunSummary,
} from "../api/client";

export function RunDetails({
  adapter,
  projectId,
  queryId,
  tapperBaseUrl,
  run,
  attempts,
  onClose,
}: {
  adapter: InsightsDataAdapter;
  projectId: string;
  queryId: string;
  tapperBaseUrl: string;
  run: RunSummary;
  attempts: AttemptDetail[];
  onClose(): void;
}) {
  const [evidenceError, setEvidenceError] = useState("");
  // Every attempt from one receipt shares a single evidence document.
  const evidence = useRef(new Map<string, Promise<ReportEvidence>>());
  const loadEvidence = (receiptId: string) => {
    let pending = evidence.current.get(receiptId);
    if (!pending) {
      pending = adapter.getReportEvidence(projectId, receiptId);
      pending.catch(() => evidence.current.delete(receiptId));
      evidence.current.set(receiptId, pending);
    }
    return pending;
  };
  const handoffUrl = new URL(tapperBaseUrl);
  handoffUrl.searchParams.set("projectId", projectId);
  handoffUrl.searchParams.set("queryId", queryId);
  handoffUrl.searchParams.set(
    "draft",
    "Explain this failed test using authorized Insights and knowledge evidence.",
  );
  run.evidenceRefs.forEach((reference) =>
    handoffUrl.searchParams.append("resourceRef", reference),
  );
  const download = async (receiptId: string) => {
    try {
      const blob = await adapter.downloadEvidence(projectId, receiptId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${receiptId}.${blob.type === "application/zip" ? "zip" : "xml"}`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      const status = (cause as { status?: number }).status;
      setEvidenceError(
        status === 401
          ? "Sign in to access this evidence."
          : status === 404 || status === 410
          ? "This evidence reference has expired or was removed."
          : status === 403
            ? "You no longer have access to this evidence."
            : "Evidence is temporarily unavailable.",
      );
    }
  };
  const instances = Array.from(
    attempts.reduce((groups, attempt) => {
      const key = `${attempt.stableTestId ?? attempt.sourceTestIdentity}\u001f${attempt.dataRow ?? ""}`;
      const group = groups.get(key) ?? [];
      group.push(attempt);
      groups.set(key, group);
      return groups;
    }, new Map<string, AttemptDetail[]>()),
  );
  return (
    <section className="ti-details" aria-label={`Run ${run.externalRunId} details`}>
      <header>
        <div><h2>{run.externalRunId}</h2><p>{run.buildId ?? "Build not reported"} · {run.branch ?? "Branch not reported"} · {run.environment}</p></div>
        <button type="button" onClick={onClose}>Close details</button>
      </header>
      <p>Run → instance → data row → attempt → raw report</p>
      <aside className="ti-tapper-handoff">
        <div>
          <strong>Prepare an investigation draft</strong>
          <p>This opens only an editable, number-free draft. It is not a verified Insights explanation.</p>
        </div>
        <a
          href={handoffUrl.toString()}
          rel="noopener noreferrer"
          target="_blank"
        >
          Open draft in Tapper
        </a>
      </aside>
      {attempts.length ? (
        <div className="ti-attempts">
          {instances.map(([key, instanceAttempts]) => (
            <article key={key} aria-label={`${instanceAttempts[0]!.stableTestId ?? instanceAttempts[0]!.sourceTestIdentity} instance`}>
              <h3>{instanceAttempts[0]!.sourceTestIdentity}</h3>
              <dl className="ti-instance-identity">
                <div><dt>Source identity</dt><dd>{instanceAttempts[0]!.sourceTestIdentity}</dd></div>
                <div><dt>Data row</dt><dd>{instanceAttempts[0]!.dataRow ?? "No data row reported"}</dd></div>
              </dl>
              <div className="ti-attempt-list">
                {instanceAttempts.map((attempt) => <section key={attempt.factKey}>
                  <h4>Attempt {attempt.attempt ?? "unknown"}</h4>
                  <dl>
                    <div><dt>Result</dt><dd>{attempt.result}</dd></div>
                    <div><dt>Duration</dt><dd>{attempt.durationSeconds === null ? "Not reported" : `${attempt.durationSeconds.toFixed(2)} s`}</dd></div>
                  </dl>
                  {attempt.evidenceRefs.map((receiptId) => <AttemptEvidence key={receiptId} adapter={adapter} projectId={projectId} receiptId={receiptId} factKey={attempt.factKey} loadEvidence={loadEvidence} />)}
                  {attempt.evidenceRefs.map((receiptId) => (
                    <button key={receiptId} type="button" onClick={() => download(receiptId)}>Download raw report</button>
                  ))}
                </section>)}
              </div>
            </article>
          ))}
        </div>
      ) : <p>No attempts are available for this run.</p>}
      {evidenceError ? <p role="alert">{evidenceError}</p> : null}
    </section>
  );
}
