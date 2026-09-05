import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Skeleton,
  Timeline,
  Typography,
} from "antd";

import { useDocumentDetailQuery } from "../api/queries";
import type {
  DocumentStageSnapshot,
  DocumentStageState,
  IngestionStage,
} from "../api/types";
import { COPY, STAGE_STATE_COPY, STAGE_TITLES, safeProblemCopy } from "../copy";

const INGESTION_STAGES: IngestionStage[] = [
  "stored",
  "parsing",
  "chunking",
  "embedding",
  "publishing",
  "ready",
];

const TIMELINE_COLOR: Readonly<Record<DocumentStageState, string>> = {
  pending: "gray",
  processing: "blue",
  completed: "green",
  failed: "red",
};

function stageSnapshot(
  stages: DocumentStageSnapshot[],
  stage: IngestionStage,
): DocumentStageSnapshot {
  return (
    stages.find((snapshot) => snapshot.stage === stage) ?? {
      completedAt: null,
      errorCode: null,
      stage,
      state: "pending",
    }
  );
}

interface DocumentDetailProps {
  documentId: string | null;
  filename: string;
  onClose: () => void;
  onAfterClose: () => void;
}

export function DocumentDetail({
  documentId,
  filename,
  onClose,
  onAfterClose,
}: DocumentDetailProps) {
  const detailQuery = useDocumentDetailQuery(documentId);

  return (
    <Drawer
      open={documentId !== null}
      title={COPY.detailTitle(filename)}
      size="min(520px, 100vw)"
      closable={false}
      focusable={{ focusTriggerAfterClose: false }}
      destroyOnHidden
      afterOpenChange={(open) => {
        if (!open) onAfterClose();
      }}
      extra={
        <Button onClick={onClose} aria-label={COPY.close}>
          {COPY.close}
        </Button>
      }
      onClose={onClose}
    >
      {detailQuery.isPending ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : null}
      {detailQuery.isError ? (
        <Alert
          type="error"
          showIcon
          title={safeProblemCopy(detailQuery.error, "detail")}
        />
      ) : null}
      {detailQuery.data !== undefined ? (
        <div className="tapper-detail-stack">
          <section aria-labelledby="source-facts-heading">
            <Typography.Title level={5} id="source-facts-heading">
              {COPY.immutableFacts}
            </Typography.Title>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label={COPY.revisionId}>
                <code>{detailQuery.data.revisionId}</code>
              </Descriptions.Item>
              <Descriptions.Item label={COPY.sourceHash}>
                <code className="tapper-hash">
                  {detailQuery.data.sourceContentHash}
                </code>
              </Descriptions.Item>
            </Descriptions>
          </section>

          <section aria-labelledby="ingestion-timeline-heading">
            <Typography.Title level={5} id="ingestion-timeline-heading">
              {COPY.ingestionTimeline}
            </Typography.Title>
            <Timeline
              items={INGESTION_STAGES.map((stage) => {
                const snapshot = stageSnapshot(detailQuery.data.stages, stage);
                return {
                  color: TIMELINE_COLOR[snapshot.state],
                  content: (
                    <div className="tapper-timeline-item">
                      <span>{STAGE_TITLES[stage]}</span>
                      <small>{STAGE_STATE_COPY[snapshot.state]}</small>
                    </div>
                  ),
                };
              })}
            />
          </section>

          <section aria-labelledby="normalized-preview-heading">
            <Typography.Title level={5} id="normalized-preview-heading">
              {COPY.normalizedPreview}
            </Typography.Title>
            <pre className="tapper-preview">
              {detailQuery.data.normalizedPreview ?? COPY.previewUnavailable}
            </pre>
          </section>
        </div>
      ) : null}
    </Drawer>
  );
}
