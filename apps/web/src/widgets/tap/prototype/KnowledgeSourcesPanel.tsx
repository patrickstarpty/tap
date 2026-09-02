import { BookOutlined } from "@ant-design/icons";
import { Checkbox, Input, Spin } from "antd";
import { useMemo, useState } from "react";

import type { PrototypeCopy } from "./copy";
import type { LibrarySource } from "./model";

interface KnowledgeSourcesPanelProps {
  copy: PrototypeCopy;
  isLoading: boolean;
  onToggleSource: (sourceId: string) => void;
  selectedSourceIds: readonly string[];
  sources: readonly LibrarySource[];
}

export function KnowledgeSourcesPanel({
  copy,
  isLoading,
  onToggleSource,
  selectedSourceIds,
  sources,
}: KnowledgeSourcesPanelProps) {
  const [query, setQuery] = useState("");
  const readySources = useMemo(
    () => sources.filter((source) => source.status === "ready"),
    [sources],
  );
  const visibleSources = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (normalized.length === 0) return readySources;
    return readySources.filter((source) =>
      source.name.toLowerCase().includes(normalized),
    );
  }, [query, readySources]);
  const selectedIds = new Set(selectedSourceIds);

  return (
    <aside className="tap-sources" aria-labelledby="tap-sources-heading">
      <header>
        <div>
          <h2 id="tap-sources-heading">{copy.sources.heading}</h2>
          <p>{copy.sources.description}</p>
        </div>
        <span className="tap-source-count">
          {selectedSourceIds.length} {copy.sources.selected}
        </span>
      </header>

      <Input
        className="tap-source-search"
        aria-label={copy.sources.search}
        placeholder={copy.sources.search}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />

      {isLoading ? (
        <div className="tap-sources-loading" aria-label={copy.sources.loading}>
          <Spin size="small" />
          <span>{copy.sources.loading}</span>
        </div>
      ) : readySources.length === 0 ? (
        <div className="tap-sources-empty">
          <BookOutlined aria-hidden="true" />
          <span>{copy.sources.noReadySources}</span>
        </div>
      ) : (
        <div className="tap-source-list">
          {visibleSources.map((source) => (
            <Checkbox
              key={source.id}
              checked={selectedIds.has(source.id)}
              onChange={() => onToggleSource(source.id)}
            >
              <span className="tap-source-name">
                <strong>{source.name}</strong>
                <small>
                  {copy.sources.ready} · {copy.sources.immutableRevision}
                </small>
              </span>
            </Checkbox>
          ))}
        </div>
      )}

      <p className="tap-source-footnote">{copy.sources.provenanceHint}</p>
    </aside>
  );
}
