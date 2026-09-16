import { Tag } from "antd";

import type { DocumentSummary } from "../api/types";
import { COPY, STAGE_COPY, STAGE_TITLES, STATUS_COPY } from "../copy";

const STATUS_COLORS = {
  queued: "default",
  processing: "processing",
  ready: "success",
  failed: "error",
  deleting: "warning",
} as const;

export function DocumentStatus({ document }: { document: DocumentSummary }) {
  const operationalCopy =
    document.status === "processing"
      ? STAGE_COPY[document.stage]
      : STATUS_COPY[document.status];

  return (
    <div className="tapper-status-cell">
      <Tag color={STATUS_COLORS[document.status]}>{operationalCopy}</Tag>
      {document.status === "failed" ? (
        <span className="tapper-stage-copy">
          {COPY.failedStage(STAGE_TITLES[document.stage])}
        </span>
      ) : null}
      {document.status === "failed" &&
      typeof document.errorSummary === "string" ? (
        <span className="tapper-safe-summary">{document.errorSummary}</span>
      ) : null}
      {document.status === "failed" &&
      typeof document.errorCode === "string" ? (
        <code className="tapper-safe-code">{document.errorCode}</code>
      ) : null}
    </div>
  );
}
