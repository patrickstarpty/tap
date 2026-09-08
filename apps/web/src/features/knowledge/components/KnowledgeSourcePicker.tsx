import { BookOutlined } from "@ant-design/icons";
import { Checkbox, Input, Spin } from "antd";
import { useState } from "react";

interface PickerSource {
  readonly id: string;
  readonly name: string;
  readonly ready: boolean;
  readonly pending: boolean;
}

interface PickerLabels {
  search: string;
  loading: string;
  noReadySources: string;
  noResults: string;
  empty: string;
  pending: string;
  error: string;
  retry: string;
  ready: string;
  selected: string;
  immutableRevision: string;
}

export function KnowledgeSourcePicker({
  labels,
  sources,
  loadState,
  selectedSourceIds,
  onToggleSource,
  onRetry,
  showSelectionCount = true,
}: {
  labels: PickerLabels;
  sources: readonly PickerSource[];
  loadState: "loading" | "loaded" | "error";
  selectedSourceIds: readonly string[];
  onToggleSource: (sourceId: string) => void;
  onRetry: () => void;
  showSelectionCount?: boolean;
}) {
  const [query, setQuery] = useState("");
  const ready = sources.filter((source) => source.ready);
  const visible = ready.filter((source) =>
    source.name.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const emptyText =
    sources.length === 0
      ? labels.empty
      : sources.some((source) => source.pending)
        ? labels.pending
        : labels.noReadySources;
  return (
    <>
      <Input
        className="tap-source-search"
        aria-label={labels.search}
        placeholder={labels.search}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {showSelectionCount && (
        <span className="tap-source-count" role="status">
          {selectedSourceIds.length} {labels.selected}
        </span>
      )}
      {loadState === "loading" ? (
        <div className="tap-sources-loading">
          <Spin size="small" />
          <span>{labels.loading}</span>
        </div>
      ) : loadState === "error" ? (
        <div className="tap-sources-empty" role="alert">
          <span>{labels.error}</span>
          <button type="button" onClick={onRetry}>
            {labels.retry}
          </button>
        </div>
      ) : visible.length === 0 ? (
        <div className="tap-sources-empty">
          <BookOutlined aria-hidden="true" />
          <span>{ready.length === 0 ? emptyText : labels.noResults}</span>
        </div>
      ) : (
        <div className="tap-source-list">
          {visible.map((source) => (
            <Checkbox
              key={source.id}
              checked={selectedSourceIds.includes(source.id)}
              onChange={() => onToggleSource(source.id)}
            >
              <span className="tap-source-name">
                <strong>{source.name}</strong>
                <small>
                  {labels.ready} · {labels.immutableRevision}
                </small>
              </span>
            </Checkbox>
          ))}
        </div>
      )}
    </>
  );
}
