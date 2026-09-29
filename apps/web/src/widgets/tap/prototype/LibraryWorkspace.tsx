import {
  AppstoreOutlined,
  BarsOutlined,
  DownloadOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";

import ReactMarkdown from "react-markdown";

import { FileTypeIcon } from "./FileTypeIcon";
import { ACCEPTED_SOURCE_EXTENSIONS, getFileTypeFamily } from "./fileTypes";
import { AccessibleDialog } from "../../../legacy/AccessibleDialog";
import type { PrototypeCopy } from "./copy";
import { GraphViewSwitch, type GraphView } from "./GraphViewSwitch";
import { KnowledgeGraph } from "./KnowledgeGraph";
import { SourceDetailDialog } from "./SourceDetailDialog";
import { UploadChunkSettings } from "./UploadChunkSettings";
import { DEFAULT_CHUNK_SETTINGS, type ChunkSettings } from "./ChunkManager";
import type { LibrarySource } from "./model";
import {
  clearPrototypeFault,
  isPrototypeFaultActive,
  takePrototypeFault,
} from "./prototypeFaults";

type LibraryMode = "list" | "graph";
type LibraryStatusFilter = "all" | LibrarySource["status"];

interface LibraryWorkspaceProps {
  copy: PrototypeCopy;
  onInspectSource?: (sourceId: string, trigger: HTMLElement) => void;
  onAddSource: (
    source: Pick<LibrarySource, "name" | "type">,
    chunkSettings: ChunkSettings,
  ) => void;
  onRetrySource: (sourceId: string) => void;
  onDeleteSource: (sourceId: string) => void;
  sources: readonly LibrarySource[];
}

function sourceType(filename: string): string {
  return filename.split(".").pop()?.toLocaleUpperCase() ?? "FILE";
}

export function LibraryWorkspace({
  copy,
  onAddSource,
  onInspectSource,
  onRetrySource,
  onDeleteSource,
  sources,
}: LibraryWorkspaceProps) {
  const [view, setView] = useState<"list" | "cards">("cards");
  const [mode, setMode] = useState<LibraryMode>("graph");
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<LibraryStatusFilter>("all");
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [addStep, setAddStep] = useState<"file" | "chunks">("file");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [chunkSettings, setChunkSettings] = useState<ChunkSettings>(
    DEFAULT_CHUNK_SETTINGS,
  );
  const [uploading, setUploading] = useState(false);
  const [uploadFailed, setUploadFailed] = useState(false);
  const [detailSourceId, setDetailSourceId] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(() =>
    isPrototypeFaultActive("library-load-failed"),
  );
  const [graphView, setGraphView] = useState<GraphView>({ kind: "domain" });
  const [graphLoadFailed, setGraphLoadFailed] = useState(() =>
    isPrototypeFaultActive("graph-load-failed"),
  );
  const addDialogTriggerRef = useRef<HTMLElement | null>(null);
  const detailTriggerRef = useRef<HTMLElement | null>(null);
  const listTabRef = useRef<HTMLButtonElement>(null);
  const graphTabRef = useRef<HTMLButtonElement>(null);
  const uploadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (uploadTimerRef.current) clearTimeout(uploadTimerRef.current);
    },
    [],
  );
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
    if (event.key === "Home") return selectMode("graph");
    if (event.key === "End") return selectMode("list");
    selectMode(currentMode === "list" ? "graph" : "list");
  };

  const openAddDialog = (event: MouseEvent<HTMLElement>) => {
    addDialogTriggerRef.current = event.currentTarget;
    setSelectedFile(null);
    setAddStep("file");
    setChunkSettings(DEFAULT_CHUNK_SETTINGS);
    setUploading(false);
    setUploadFailed(false);
    setAddDialogOpen(true);
  };

  const closeAddDialog = () => {
    if (uploading) return;
    if (uploadTimerRef.current) {
      clearTimeout(uploadTimerRef.current);
      uploadTimerRef.current = null;
    }
    setSelectedFile(null);
    setAddStep("file");
    setChunkSettings(DEFAULT_CHUNK_SETTINGS);
    setUploading(false);
    setUploadFailed(false);
    setAddDialogOpen(false);
  };

  const openDetail = (sourceId: string, trigger: HTMLElement) => {
    detailTriggerRef.current = trigger;
    setDetailSourceId(sourceId);
  };

  const closeDetail = () => setDetailSourceId(null);

  const detailSource =
    sources.find((source) => source.id === detailSourceId) ?? null;

  const goToChunkStep = () => {
    if (selectedFile === null) return;
    setAddStep("chunks");
  };

  const goToFileStep = () => {
    setAddStep("file");
  };

  const addSource = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (selectedFile === null || uploading) return;
    const file = selectedFile;
    const settings = chunkSettings;
    const failed = takePrototypeFault("upload-failed");
    setUploadFailed(false);
    setUploading(true);
    uploadTimerRef.current = setTimeout(() => {
      uploadTimerRef.current = null;
      setUploading(false);
      if (failed) {
        setUploadFailed(true);
        return;
      }
      onAddSource({ name: file.name, type: sourceType(file.name) }, settings);
      closeAddDialog();
    }, 1000);
  };

  const sourceStatus = (source: LibrarySource) => {
    if (source.status === "ready") return copy.library.ready;
    if (source.status === "failed") return copy.library.failed;
    return copy.library.processing;
  };

  return (
    <section
      className={`tap-module tap-library${mode === "graph" ? " tap-library--graph" : ""}`}
      aria-labelledby="library-heading"
    >
      <header className="tap-module-heading">
        <div>
          <h1 id="library-heading">{copy.library.heading}</h1>
          <p>{copy.library.description}</p>
        </div>
        <div className="tap-library-heading-actions">
          <Button
            type="primary"
            icon={<PlusOutlined aria-hidden="true" />}
            onClick={openAddDialog}
          >
            {copy.library.addSource}
          </Button>
        </div>
      </header>

      {loadFailed ? (
        <div className="tap-library-load-error">
          <p role="alert">{copy.library.loadFailed}</p>
          <Button
            onClick={() => {
              clearPrototypeFault("library-load-failed");
              setLoadFailed(false);
            }}
          >
            {copy.navigation.retry}
          </Button>
        </div>
      ) : sources.length === 0 ? (
        <div className="tap-library-empty">
          <p>{copy.library.emptyHeading}</p>
          <Button
            type="primary"
            icon={<PlusOutlined aria-hidden="true" />}
            onClick={openAddDialog}
          >
            {copy.library.addSource}
          </Button>
        </div>
      ) : (
        <>
        <div className="tap-library-toolbar">
          <div
            className="tap-section-tabs"
            role="tablist"
            aria-label={copy.library.heading}
          >
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
          </div>
          <div className="tap-library-filters">
            <div className="tap-library-search-actions">
              <Input
                className="tap-library-search"
                aria-label={copy.library.search}
                placeholder={copy.library.search}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <Button
                className="tap-library-clear"
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
          </div>
        </div>

        {mode === "list" ? (
          <div
            id="tap-library-list-panel"
            role="tabpanel"
            aria-labelledby="tap-library-list-tab"
          >
            <div
              className="tap-library-view-switch"
              role="group"
              aria-label={copy.library.sources}
            >
              <Button
                type="text"
                aria-label={copy.library.cardView}
                title={copy.library.cardView}
                aria-pressed={view === "cards"}
                icon={<AppstoreOutlined />}
                onClick={() => setView("cards")}
              />
              <Button
                type="text"
                aria-label={copy.library.listView}
                title={copy.library.listView}
                aria-pressed={view === "list"}
                icon={<BarsOutlined />}
                onClick={() => setView("list")}
              />
            </div>
            <div className="tap-library-browser">
              <div>
                {visibleSources.length === 0 ? (
                  <div className="tap-catalog-empty">
                    {copy.library.noResults}
                  </div>
                ) : (
                  <ul
                    className={`tap-library-list tap-file-list${view === "cards" ? " tap-file-list--cards" : ""}`}
                    aria-label={copy.library.sources}
                  >
                    {visibleSources.map((source) => (
                      <li key={source.id}>
                        <div className="tap-file-summary">
                          <FileTypeIcon type={source.type} />
                          <span className="tap-library-source-copy">
                            <strong>{source.name}</strong>
                            <span>
                              {source.isExample
                                ? `${copy.library.example} · `
                                : ""}
                              {source.description}
                            </span>
                          </span>
                          {view === "cards" ? (
                            <div
                              className="tap-file-card-content"
                              aria-hidden="true"
                            >
                              <FileContent
                                source={source}
                                fallback={copy.library.noPreview}
                              />
                            </div>
                          ) : null}
                        </div>
                        <div className="tap-file-actions">
                          <span
                            className="tap-library-status"
                            data-status={source.status}
                          >
                            {sourceStatus(source)}
                          </span>
                          {source.reviewState && onInspectSource ? (
                            <Button
                              type="text"
                              aria-label={`${copy.navigation.library === "Library" ? "Manage chunks" : "管理切片"} ${source.name}`}
                              onClick={(event) =>
                                onInspectSource(source.id, event.currentTarget)
                              }
                            >
                              {copy.navigation.library === "Library"
                                ? "Manage chunks"
                                : "管理切片"}
                            </Button>
                          ) : null}
                          <Button
                            type="text"
                            aria-label={`${copy.library.viewSourceButton} ${source.name}`}
                            onClick={(event) =>
                              openDetail(source.id, event.currentTarget)
                            }
                          >
                            {copy.library.viewSourceButton}
                          </Button>
                          {source.downloadUrl ? (
                            <a
                              className="tap-file-download"
                              href={source.downloadUrl}
                              download={source.name}
                              aria-label={`${copy.library.download} ${source.name}`}
                              title={copy.library.download}
                            >
                              <DownloadOutlined aria-hidden="true" />
                            </a>
                          ) : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div
            id="tap-library-graph-panel"
            role="tabpanel"
            aria-labelledby="tap-library-graph-tab"
          >
            {graphLoadFailed ? (
              <div className="tap-library-load-error">
                <p role="alert">{copy.library.graphLoadFailed}</p>
                <Button
                  onClick={() => {
                    clearPrototypeFault("graph-load-failed");
                    setGraphLoadFailed(false);
                  }}
                >
                  {copy.navigation.retry}
                </Button>
              </div>
            ) : (
              <>
                <GraphViewSwitch
                  copy={copy}
                  sources={facetSources}
                  value={graphView}
                  onChange={setGraphView}
                />
                {graphView.kind === "domain" ? (
                  <KnowledgeGraph
                    copy={copy}
                    query={query}
                    sources={facetSources}
                    onViewSource={(source) => {
                      setQuery(source.name);
                      setMode("list");
                      listTabRef.current?.focus();
                    }}
                  />
                ) : (
                  (() => {
                    const selectedSource =
                      sources.find(
                        (source) => source.id === graphView.sourceId,
                      ) ?? null;
                    if (!selectedSource?.hasPublishedGraph) {
                      return (
                        <div className="tap-catalog-empty">
                          {copy.library.graphEmpty}
                        </div>
                      );
                    }
                    return (
                      <KnowledgeGraph
                        copy={copy}
                        query={query}
                        sources={[selectedSource]}
                        onViewSource={(source) => {
                          setQuery(source.name);
                          setMode("list");
                          listTabRef.current?.focus();
                        }}
                      />
                    );
                  })()
                )}
              </>
            )}
          </div>
        )}
        </>
      )}

      {addDialogOpen ? (
        <AccessibleDialog
          ariaLabel={copy.library.addSource}
          className="tap-catalog-dialog tap-source-dialog"
          onClose={closeAddDialog}
          opener={addDialogTriggerRef.current}
        >
          <header>
            <h2>
              {addStep === "file"
                ? copy.library.stepFile
                : copy.library.stepChunks}
            </h2>
          </header>
          <form onSubmit={addSource}>
            {addStep === "file" ? (
              <>
                <label>
                  <span>{copy.library.sourceFile}</span>
                  <input
                    type="file"
                    aria-label={copy.library.sourceFile}
                    accept={ACCEPTED_SOURCE_EXTENSIONS}
                    onChange={(event) =>
                      setSelectedFile(event.target.files?.[0] ?? null)
                    }
                  />
                </label>
                <p>{copy.library.supportedFormats}</p>
                <div className="tap-dialog-actions">
                  <Button onClick={closeAddDialog}>
                    {copy.library.cancel}
                  </Button>
                  <Button
                    type="primary"
                    htmlType="button"
                    disabled={selectedFile === null}
                    onClick={goToChunkStep}
                  >
                    {copy.library.next}
                  </Button>
                </div>
              </>
            ) : (
              <>
                <UploadChunkSettings
                  copy={copy}
                  value={chunkSettings}
                  onChange={setChunkSettings}
                />
                <p>{copy.library.recommended}</p>
                {uploading ? (
                  <p role="status">{copy.library.uploading}</p>
                ) : null}
                {uploadFailed ? (
                  <p role="alert">{copy.library.uploadFailed}</p>
                ) : null}
                <div className="tap-dialog-actions">
                  <Button onClick={closeAddDialog} disabled={uploading}>
                    {copy.library.cancel}
                  </Button>
                  <Button
                    htmlType="button"
                    onClick={goToFileStep}
                    disabled={uploading}
                  >
                    {copy.library.back}
                  </Button>
                  <Button
                    type="primary"
                    htmlType="submit"
                    loading={uploading}
                    aria-busy={uploading}
                    aria-label={copy.library.addSource}
                  >
                    {copy.library.addSource}
                  </Button>
                </div>
              </>
            )}
          </form>
        </AccessibleDialog>
      ) : null}

      {detailSource ? (
        <SourceDetailDialog
          copy={copy}
          source={detailSource}
          opener={detailTriggerRef.current}
          onClose={closeDetail}
          onRetry={onRetrySource}
          onDelete={onDeleteSource}
        />
      ) : null}
    </section>
  );
}

function FileContent({
  source,
  fallback,
}: {
  source: LibrarySource;
  fallback: string;
}) {
  if (source.preview?.imageUrl)
    return <img src={source.preview.imageUrl} alt={source.name} />;
  if (source.preview?.text)
    return getFileTypeFamily(source.type) === "markdown" ? (
      <ReactMarkdown>{source.preview.text}</ReactMarkdown>
    ) : (
      <pre>{source.preview.text}</pre>
    );
  return (
    <div className="tap-file-unavailable">
      <FileTypeIcon type={source.type} />
      <p>{fallback}</p>
    </div>
  );
}
