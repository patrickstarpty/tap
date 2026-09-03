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
  type MouseEvent,
} from "react";

import { AccessibleDialog } from "./AccessibleDialog";
import type { PrototypeCopy } from "./copy";
import { KnowledgeGraph } from "./KnowledgeGraph";
import type { LibrarySource } from "./model";

type LibraryMode = "list" | "graph";
type LibraryStatusFilter = "all" | LibrarySource["status"];

interface LibraryWorkspaceProps {
  copy: PrototypeCopy;
  onAddSource: (source: Pick<LibrarySource, "name" | "type">) => void;
  sources: readonly LibrarySource[];
}

function sourceType(filename: string): string {
  return filename.split(".").pop()?.toLocaleUpperCase() ?? "FILE";
}

export function LibraryWorkspace({
  copy,
  onAddSource,
  sources,
}: LibraryWorkspaceProps) {
  const [mode, setMode] = useState<LibraryMode>("list");
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<LibraryStatusFilter>("all");
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const addDialogTriggerRef = useRef<HTMLElement | null>(null);
  const listTabRef = useRef<HTMLButtonElement>(null);
  const graphTabRef = useRef<HTMLButtonElement>(null);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const availableTypes = useMemo(
    () => [...new Set(sources.map((source) => source.type))].sort(),
    [sources],
  );
  const facetSources = useMemo(
    () =>
      sources.filter(
        (source) =>
          (typeFilter === "all" || source.type === typeFilter) &&
          (statusFilter === "all" || source.status === statusFilter),
      ),
    [sources, statusFilter, typeFilter],
  );
  const visibleSources = useMemo(
    () =>
      normalizedQuery.length === 0
        ? facetSources
        : facetSources.filter((source) =>
            [source.name, source.type, source.description].some((value) =>
              value.toLocaleLowerCase().includes(normalizedQuery),
            ),
          ),
    [facetSources, normalizedQuery],
  );
  const filtersActive =
    normalizedQuery.length > 0 ||
    typeFilter !== "all" ||
    statusFilter !== "all";

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

  const openAddDialog = (event: MouseEvent<HTMLElement>) => {
    addDialogTriggerRef.current = event.currentTarget;
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
            {copy.library.all}
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
        <div className="tap-library-filters">
          <Input
            className="tap-library-search"
            aria-label={copy.library.search}
            placeholder={copy.library.search}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <label>
            <span>{copy.library.typeFilter}</span>
            <select
              aria-label={copy.library.typeFilter}
              value={typeFilter}
              onChange={(event) => setTypeFilter(event.target.value)}
            >
              <option value="all">{copy.library.allTypes}</option>
              {availableTypes.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{copy.library.statusFilter}</span>
            <select
              aria-label={copy.library.statusFilter}
              value={statusFilter}
              onChange={(event) =>
                setStatusFilter(event.target.value as LibraryStatusFilter)
              }
            >
              <option value="all">{copy.library.allStatuses}</option>
              <option value="ready">{copy.library.ready}</option>
              <option value="processing">{copy.library.processing}</option>
              <option value="failed">{copy.library.failed}</option>
            </select>
          </label>
          <span className="tap-library-result-count" aria-live="polite">
            {visibleSources.length}/{sources.length} {copy.library.sourceCount}
          </span>
          <Button
            type="text"
            disabled={!filtersActive}
            onClick={() => {
              setQuery("");
              setTypeFilter("all");
              setStatusFilter("all");
            }}
          >
            {copy.library.clearFilters}
          </Button>
        </div>
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
          <KnowledgeGraph copy={copy} query={query} sources={facetSources} />
        </div>
      )}

      {addDialogOpen ? (
        <AccessibleDialog
          ariaLabel={copy.library.addSource}
          className="tap-catalog-dialog tap-source-dialog"
          onClose={closeAddDialog}
          opener={addDialogTriggerRef.current}
        >
          <header>
            <h2>{copy.library.addSource}</h2>
          </header>
          <form onSubmit={addSource}>
            <label>
              <span>{copy.library.sourceFile}</span>
              <input
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
        </AccessibleDialog>
      ) : null}
    </section>
  );
}
