import {
  Alert,
  Button,
  Checkbox,
  Input,
  Skeleton,
  Tag,
  Typography,
} from "antd";
import { useMemo, useState } from "react";

import type { DocumentSummary } from "../api/types";
import { COPY, STAGE_COPY, STATUS_COPY } from "../copy";
import { MAX_SELECTED_SOURCES } from "../model/sourceSelection";

interface SourcesPanelProps {
  documents: readonly DocumentSummary[];
  error: unknown;
  isError: boolean;
  isPending: boolean;
  selectedIds: readonly string[];
  onClear: () => void;
  onRetry: () => void;
  onSelectAllReady: () => void;
  onToggle: (sourceId: string) => void;
}

function sourceStatus(document: DocumentSummary): string {
  return document.status === "processing"
    ? STAGE_COPY[document.stage]
    : STATUS_COPY[document.status];
}

export function SourcesPanel({
  documents,
  isError,
  isPending,
  selectedIds,
  onClear,
  onRetry,
  onSelectAllReady,
  onToggle,
}: SourcesPanelProps) {
  const [search, setSearch] = useState("");
  const selected = new Set(selectedIds);
  const normalizedSearch = search.trim().toLocaleLowerCase("zh-CN");
  const visibleDocuments = useMemo(
    () =>
      documents.filter((document) => {
        if (normalizedSearch.length === 0) return true;
        return `${document.filename}\n${document.documentId}`
          .toLocaleLowerCase("zh-CN")
          .includes(normalizedSearch);
      }),
    [documents, normalizedSearch],
  );
  const readyCount = documents.filter(
    (document) => document.status === "ready",
  ).length;
  const atLimit = selectedIds.length >= MAX_SELECTED_SOURCES;

  return (
    <section
      className="tapper-panel tapper-sources-panel"
      aria-labelledby="sources-heading"
    >
      <header className="tapper-panel-header">
        <div>
          <Typography.Title level={3} id="sources-heading">
            {COPY.sourcesTitle}
          </Typography.Title>
          <Typography.Paragraph type="secondary">
            {COPY.sourcesDescription}
          </Typography.Paragraph>
        </div>
        <Tag>{COPY.selectedSources(selectedIds.length)}</Tag>
      </header>

      <Input
        className="tapper-source-search"
        type="search"
        allowClear
        aria-label={COPY.searchSources}
        placeholder={COPY.searchSources}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      <div className="tapper-source-actions">
        <Button disabled={readyCount === 0} onClick={onSelectAllReady}>
          {COPY.selectAllReady}
        </Button>
        <Button disabled={selectedIds.length === 0} onClick={onClear}>
          {COPY.clearSelection}
        </Button>
      </div>

      {atLimit && readyCount > MAX_SELECTED_SOURCES ? (
        <p className="tapper-source-limit" role="status">
          {COPY.sourceLimit}
        </p>
      ) : null}
      {isPending ? (
        <div className="tapper-source-loading" aria-label={COPY.sourcesLoading}>
          <Skeleton active paragraph={{ rows: 7 }} />
        </div>
      ) : null}
      {isError ? (
        <Alert
          type="error"
          showIcon
          title={COPY.sourcesFailure}
          action={
            <Button size="small" onClick={onRetry}>
              {COPY.retry}
            </Button>
          }
        />
      ) : null}
      {!isPending && !isError && documents.length === 0 ? (
        <div className="tapper-source-empty">
          <strong>{COPY.sourcesEmpty}</strong>
          <span>{COPY.sourcesEmptyDescription}</span>
        </div>
      ) : null}
      {!isPending && !isError && documents.length > 0 ? (
        <ul className="tapper-source-list">
          {visibleDocuments.map((document) => {
            const checked = selected.has(document.documentId);
            const isReady = document.status === "ready";
            const disabled = !isReady || (atLimit && !checked);
            const status = sourceStatus(document);
            return (
              <li key={document.documentId}>
                <Checkbox
                  checked={checked}
                  disabled={disabled}
                  aria-label={`${document.filename} · ${document.documentId} · ${status}`}
                  onChange={() => onToggle(document.documentId)}
                >
                  <span className="tapper-source-label">
                    <strong>{document.filename}</strong>
                    <small>{document.documentId}</small>
                    <span>{status}</span>
                  </span>
                </Checkbox>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
