import {
  FileMarkdownOutlined,
  FileTextOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import {
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import type { PrototypeCopy } from "./copy";
import type { LibrarySource } from "./model";

type LibraryMode = "list" | "graph";

interface LibraryWorkspaceProps {
  copy: PrototypeCopy;
  onAddSource: (source: Pick<LibrarySource, "name" | "type">) => void;
  sources: readonly LibrarySource[];
}

function sourceType(filename: string): string {
  return filename.split(".").pop()?.toLocaleUpperCase() ?? "FILE";
}

function GraphNode({
  accent = false,
  label,
  secondary,
  width,
  x,
  y,
}: {
  accent?: boolean;
  label: string;
  secondary?: string;
  width: number;
  x: number;
  y: number;
}) {
  return (
    <g
      className={
        accent ? "tap-graph-node tap-graph-node--accent" : "tap-graph-node"
      }
    >
      <rect x={x} y={y} width={width} height="70" rx="12" />
      <text x={x + 16} y={y + (secondary === undefined ? 41 : 31)}>
        {label}
      </text>
      {secondary === undefined ? null : (
        <text className="tap-graph-secondary" x={x + 16} y={y + 50}>
          {secondary}
        </text>
      )}
    </g>
  );
}

function KnowledgeGraph({
  copy,
  sources,
}: {
  copy: PrototypeCopy;
  sources: readonly LibrarySource[];
}) {
  const documentNodes = sources.slice(0, 4);

  return (
    <figure className="tap-knowledge-graph">
      <svg
        role="img"
        aria-label={copy.library.knowledgeGraphImage}
        aria-describedby="tap-library-graph-caption"
        viewBox="0 0 920 520"
        preserveAspectRatio="xMidYMid meet"
      >
        <title>{copy.library.knowledgeGraphImage}</title>
        <defs>
          <marker
            id="tap-graph-arrow"
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>

        <g className="tap-graph-edge">
          <path d="M 480 122 C 535 122, 548 205, 600 205" />
          <text x="526" y="151">
            {copy.library.requires}
          </text>
          <path d="M 690 240 C 690 260, 690 275, 690 300" />
          <text x="706" y="273">
            {copy.library.informs}
          </text>
          <path d="M 480 122 C 550 122, 532 445, 600 445" />
          <text x="516" y="340">
            {copy.library.names}
          </text>
          {documentNodes.map((source, index) => {
            const y = 44 + index * 112;
            const targetY = source.name.toLocaleLowerCase().includes("health")
              ? 205
              : 122;
            return (
              <g key={source.id}>
                <path
                  d={`M 280 ${y + 35} C 345 ${y + 35}, 350 ${targetY}, 400 ${targetY}`}
                />
                <text x="312" y={Math.min(y + 20, targetY - 10)}>
                  {copy.library.supports}
                </text>
              </g>
            );
          })}
        </g>

        {documentNodes.map((source, index) => (
          <GraphNode
            key={source.id}
            label={
              source.name.length > 27
                ? `${source.name.slice(0, 24)}…`
                : source.name
            }
            secondary={source.type}
            width={240}
            x={40}
            y={44 + index * 112}
          />
        ))}
        <GraphNode
          accent
          label={copy.library.application}
          secondary="寿险投保"
          width={220}
          x={400}
          y={87}
        />
        <GraphNode
          label={copy.library.healthDisclosure}
          secondary="健康告知"
          width={220}
          x={600}
          y={170}
        />
        <GraphNode
          label={copy.library.underwriting}
          secondary="核保"
          width={220}
          x={600}
          y={300}
        />
        <GraphNode
          label={copy.library.beneficiary}
          secondary="受益人"
          width={220}
          x={600}
          y={410}
        />
      </svg>
      <figcaption id="tap-library-graph-caption">
        {copy.library.illustrative}
      </figcaption>
    </figure>
  );
}

export function LibraryWorkspace({
  copy,
  onAddSource,
  sources,
}: LibraryWorkspaceProps) {
  const [mode, setMode] = useState<LibraryMode>("list");
  const [query, setQuery] = useState("");
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const listTabRef = useRef<HTMLButtonElement>(null);
  const graphTabRef = useRef<HTMLButtonElement>(null);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const visibleSources = useMemo(
    () =>
      normalizedQuery.length === 0
        ? sources
        : sources.filter((source) =>
            [source.name, source.type, source.description].some((value) =>
              value.toLocaleLowerCase().includes(normalizedQuery),
            ),
          ),
    [normalizedQuery, sources],
  );

  const selectMode = (nextMode: LibraryMode) => {
    setMode(nextMode);
    (nextMode === "list" ? listTabRef : graphTabRef).current?.focus();
  };

  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    currentMode: LibraryMode,
  ) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    if (event.key === "Home") return selectMode("list");
    if (event.key === "End") return selectMode("graph");
    selectMode(currentMode === "list" ? "graph" : "list");
  };

  const openAddDialog = () => {
    setSelectedFile(null);
    setAddDialogOpen(true);
  };

  const closeAddDialog = () => {
    setSelectedFile(null);
    setAddDialogOpen(false);
  };

  const addSource = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (selectedFile === null) return;
    onAddSource({
      name: selectedFile.name,
      type: sourceType(selectedFile.name),
    });
    closeAddDialog();
  };

  const sourceStatus = (source: LibrarySource) => {
    if (source.status === "ready") return copy.library.ready;
    if (source.status === "failed") return copy.library.failed;
    return copy.library.processing;
  };

  return (
    <section
      className="tap-module tap-library"
      aria-labelledby="library-heading"
    >
      <header className="tap-module-heading">
        <div>
          <h1 id="library-heading">{copy.library.heading}</h1>
          <p>{copy.library.description}</p>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined aria-hidden="true" />}
          onClick={openAddDialog}
        >
          {copy.library.addSource}
        </Button>
      </header>

      <div className="tap-library-toolbar">
        <div
          className="tap-section-tabs"
          role="tablist"
          aria-label={copy.library.heading}
        >
          <button
            ref={listTabRef}
            id="tap-library-list-tab"
            type="button"
            role="tab"
            aria-selected={mode === "list"}
            aria-controls="tap-library-list-panel"
            tabIndex={mode === "list" ? 0 : -1}
            onClick={() => setMode("list")}
            onKeyDown={(event) => handleTabKeyDown(event, "list")}
          >
            {copy.library.thumbnailList}
          </button>
          <button
            ref={graphTabRef}
            id="tap-library-graph-tab"
            type="button"
            role="tab"
            aria-selected={mode === "graph"}
            aria-controls="tap-library-graph-panel"
            tabIndex={mode === "graph" ? 0 : -1}
            onClick={() => setMode("graph")}
            onKeyDown={(event) => handleTabKeyDown(event, "graph")}
          >
            {copy.library.knowledgeGraph}
          </button>
        </div>
        <Input
          aria-label={copy.library.search}
          placeholder={copy.library.search}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {mode === "list" ? (
        <div
          id="tap-library-list-panel"
          role="tabpanel"
          aria-labelledby="tap-library-list-tab"
        >
          {visibleSources.length === 0 ? (
            <div className="tap-catalog-empty">{copy.library.noResults}</div>
          ) : (
            <ul className="tap-library-list" aria-label={copy.library.sources}>
              {visibleSources.map((source) => (
                <li key={source.id}>
                  <div className="tap-library-thumbnail" aria-hidden="true">
                    {source.type === "MD" ? (
                      <FileMarkdownOutlined />
                    ) : (
                      <FileTextOutlined />
                    )}
                    <span>{source.type}</span>
                  </div>
                  <div className="tap-library-source-copy">
                    <strong>{source.name}</strong>
                    <span>{source.description}</span>
                  </div>
                  <span
                    className="tap-library-status"
                    data-status={source.status}
                  >
                    {sourceStatus(source)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div
          id="tap-library-graph-panel"
          role="tabpanel"
          aria-labelledby="tap-library-graph-tab"
        >
          <KnowledgeGraph copy={copy} sources={visibleSources} />
        </div>
      )}

      {addDialogOpen ? (
        <div className="tap-picker-backdrop">
          <section
            className="tap-catalog-dialog tap-source-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={copy.library.addSource}
            onKeyDown={(event) => {
              if (event.key === "Escape") closeAddDialog();
            }}
          >
            <header>
              <h2>{copy.library.addSource}</h2>
            </header>
            <form onSubmit={addSource}>
              <label>
                <span>{copy.library.sourceFile}</span>
                <input
                  autoFocus
                  type="file"
                  aria-label={copy.library.sourceFile}
                  accept=".pdf,.docx,.md,.txt"
                  onChange={(event) =>
                    setSelectedFile(event.target.files?.item(0) ?? null)
                  }
                />
              </label>
              <div className="tap-dialog-actions">
                <Button onClick={closeAddDialog}>{copy.library.cancel}</Button>
                <Button
                  type="primary"
                  htmlType="submit"
                  disabled={selectedFile === null}
                >
                  {copy.library.addSource}
                </Button>
              </div>
            </form>
          </section>
        </div>
      ) : null}
    </section>
  );
}
