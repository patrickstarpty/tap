import { useEffect, useState } from "react";
import type {
  InsightsDataAdapter,
  ReportEvidence,
  ReportStep,
} from "../api/client";

export function AttemptEvidence({
  adapter,
  projectId,
  receiptId,
  factKey,
  loadEvidence,
}: {
  adapter: InsightsDataAdapter;
  projectId: string;
  receiptId: string;
  factKey: string;
  loadEvidence?(receiptId: string): Promise<ReportEvidence>;
}) {
  const [details, setDetails] = useState<ReportEvidence | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [image, setImage] = useState<{ url: string; name: string } | null>(
    null,
  );
  useEffect(
    () => () => {
      if (image) URL.revokeObjectURL(image.url);
    },
    [image],
  );
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setDetails(
        await (loadEvidence
          ? loadEvidence(receiptId)
          : adapter.getReportEvidence(projectId, receiptId)),
      );
    } catch {
      setError("Report evidence is unavailable or access has expired.");
    } finally {
      setLoading(false);
    }
  };
  const open = async (source: string, name: string) => {
    setError("");
    try {
      const blob = await adapter.downloadAttachment(
        projectId,
        receiptId,
        source,
      );
      const url = URL.createObjectURL(blob);
      if (["image/png", "image/jpeg"].includes(blob.type)) {
        setImage({ url, name });
      } else {
        const link = document.createElement("a");
        link.href = url;
        link.download = name.replace(/[/\\]/g, "_");
        link.click();
        URL.revokeObjectURL(url);
      }
    } catch {
      setError("Attachment is unavailable or access has expired.");
    }
  };
  // Receipts projected by the frozen v1 payload store the legacy fact key.
  const node = details?.attempts.find(
    (item) => item.factKey === factKey || item.legacyFactKey === factKey,
  );
  const renderNode = (step: ReportStep, key: string) => (
    <li key={key}>
      <strong>{step.name}</strong> <span data-status={step.status}>{step.status}</span>
      {step.message && <p>{step.message}</p>}
      {step.trace && (
        <details>
          <summary>Failure details</summary>
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
            {step.trace}
          </pre>
        </details>
      )}
      {step.attachments.map((attachment, index) =>
        attachment.available ? (
          <button
            key={index}
            type="button"
            onClick={() => void open(attachment.source, attachment.name)}
          >
            Open {attachment.name}
          </button>
        ) : (
          <p key={index}>{attachment.name}: attachment missing from report</p>
        ),
      )}
      {step.steps.length > 0 && (
        <ol>
          {step.steps.map((child, index) =>
            renderNode(child, `${key}-${index}`),
          )}
        </ol>
      )}
    </li>
  );
  return (
    <div className="ti-evidence">
      {!details && (
        <button type="button" disabled={loading} onClick={() => void load()}>
          {loading ? "Loading evidence…" : "View report evidence"}
        </button>
      )}
      {node && (
        <>
          <ol>{renderNode(node, factKey)}</ol>
          {node.steps.length === 0 && (
            <p>No step details were included in this report.</p>
          )}
        </>
      )}
      {details && !node && (
        <p>This attempt has no matching evidence in the report.</p>
      )}
      {image && (
        <div>
          <img
            src={image.url}
            alt={image.name}
            style={{ maxWidth: "100%", maxHeight: 480 }}
          />
          <button type="button" onClick={() => setImage(null)}>
            Close image
          </button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
