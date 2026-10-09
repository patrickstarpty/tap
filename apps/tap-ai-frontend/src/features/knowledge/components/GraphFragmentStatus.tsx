import { useState } from "react";
import { Button, Space, Typography } from "antd";

import {
  useGraphProjectQuery,
  useKnowledgeClient,
  useRetryGraphFragmentMutation,
} from "../api/queries";
import { GRAPH_FRAGMENT_COPY } from "../copy";

export interface GraphFragmentStatusProps {
  revisionId: string;
  locale?: "en" | "zh";
}

/**
 * Shows this document revision's status in the project knowledge graph and
 * (for `PARTIAL`) offers a retry of its failed extraction batches.
 *
 * `ProjectGraphView` (backend) only exposes `extractingRevisionIds` /
 * `partialRevisionIds` — a revision missing from both is `READY`; there is
 * no per-revision failed/total batch count, so the `PARTIAL` label carries
 * no counts. A retry rejected with `graph-job-busy` (409) is a normal,
 * localized error — not the graph-version-conflict notice used elsewhere
 * for graph queries.
 */
export function GraphFragmentStatus({
  revisionId,
  locale = "zh",
}: GraphFragmentStatusProps) {
  const { projectId } = useKnowledgeClient();
  const text = GRAPH_FRAGMENT_COPY[locale];
  const projectQuery = useGraphProjectQuery(projectId);
  const retryMutation = useRetryGraphFragmentMutation(projectId);
  const [queued, setQueued] = useState(false);

  const project = projectQuery.data;
  if (project === undefined) return null;

  const isPartial = project.partialRevisionIds?.includes(revisionId) ?? false;
  const isExtracting =
    !isPartial &&
    (project.extractingRevisionIds?.includes(revisionId) ?? false);
  const statusLabel = isPartial
    ? text.partial
    : isExtracting
      ? text.extracting
      : text.ready;

  return (
    <section aria-labelledby="graph-fragment-status-heading">
      <Typography.Title level={5} id="graph-fragment-status-heading">
        {text.title}
      </Typography.Title>
      <Space orientation="vertical" size="small">
        <span>{statusLabel}</span>
        {isPartial ? (
          queued ? (
            <span>{text.retryQueued}</span>
          ) : (
            <Button
              size="small"
              loading={retryMutation.isPending}
              onClick={() => {
                retryMutation.mutate(
                  { revisionId, idempotencyKey: crypto.randomUUID() },
                  { onSuccess: () => setQueued(true) },
                );
              }}
            >
              {text.retry}
            </Button>
          )
        ) : null}
        {retryMutation.isError ? (
          <Typography.Text type="danger" role="alert">
            {text.retryFailure}
          </Typography.Text>
        ) : null}
      </Space>
    </section>
  );
}
