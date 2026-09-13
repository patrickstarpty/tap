import { KnowledgeSourcePicker } from "../../../features/knowledge/components/KnowledgeSourcePicker";

import type { PrototypeCopy } from "./copy";
import type { LibrarySource } from "./model";
import { PanelToggleIcon } from "./PanelToggleIcon";

interface KnowledgeSourcesPanelProps {
  copy: PrototypeCopy;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  onCollapse: () => void;
  onToggleSource: (sourceId: string) => void;
  selectedSourceIds: readonly string[];
  sources: readonly LibrarySource[];
}

export function KnowledgeSourcesPanel({
  copy,
  isLoading,
  isError,
  onRetry,
  onCollapse,
  onToggleSource,
  selectedSourceIds,
  sources,
}: KnowledgeSourcesPanelProps) {
  return (
    <aside
      id="tap-knowledge-sources"
      className="tap-sources"
      aria-labelledby="tap-sources-heading"
    >
      <header>
        <button
          type="button"
          className="tap-panel-toggle tap-panel-toggle--right-collapse"
          aria-controls="tap-knowledge-sources"
          aria-expanded="true"
          aria-label={copy.sources.collapse}
          onClick={onCollapse}
        >
          <PanelToggleIcon side="right" state="expanded" />
        </button>
        <div>
          <h2 id="tap-sources-heading">{copy.sources.heading}</h2>
          <p>{copy.sources.description}</p>
        </div>
        <span className="tap-source-count" role="status">
          {selectedSourceIds.length} {copy.sources.selected}
        </span>
      </header>

      <KnowledgeSourcePicker
        labels={copy.sources}
        showSelectionCount={false}
        sources={sources.map((source) => ({
          id: source.id,
          name: source.name,
          ready: source.status === "ready",
          pending: source.status === "processing",
        }))}
        loadState={isLoading ? "loading" : isError ? "error" : "loaded"}
        selectedSourceIds={selectedSourceIds}
        onToggleSource={onToggleSource}
        onRetry={onRetry}
      />

      <p className="tap-source-footnote">{copy.sources.provenanceHint}</p>
    </aside>
  );
}
