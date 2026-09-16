import {
  AppstoreOutlined,
  BarsOutlined,
  DownloadOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import { useQuery } from "@tanstack/react-query";
import {
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";

import ReactMarkdown from "react-markdown";

import { FileTypeIcon } from "./FileTypeIcon";
import { getFileTypeFamily } from "./fileTypes";
import { AccessibleDialog } from "./AccessibleDialog";
import type { PrototypeCopy } from "./copy";
import { KnowledgeGraph } from "./KnowledgeGraph";
import {
  useActiveGraph,
  useGraphSearch,
} from "../../../features/graph/api/queries";
import { publishedGraphData } from "./publishedGraphData";
import { createKnowledgeClient } from "../../../features/knowledge/api/client";
import type { LibrarySource } from "./model";

type LibraryMode = "list" | "graph";
type LibraryStatusFilter = "all" | LibrarySource["status"];

interface LibraryWorkspaceProps {
  copy: PrototypeCopy;
  onAddSource?: (file: File) => Promise<void> | void;
  onInspectSource?: (sourceId: string, opener: HTMLElement) => void;
  sources: readonly LibrarySource[];
  loadState?: "loading" | "loaded" | "error";
  onReload?: () => void;
  graphProjectId?: string;
  locale?: "en" | "zh";
}

function ProjectKnowledgeGraph({
  projectId,
  sources,
  locale,
  copy,
  query,
  onViewSource,
}: {
  projectId: string;
  sources: readonly LibrarySource[];
  locale: "en" | "zh";
  copy: PrototypeCopy;
  query: string;
  onViewSource: (source: LibrarySource) => void;
}) {
  const readySources = sources.filter((source) => source.status === "ready");
  const [graphView, setGraphView] = useState<"overview" | "published">(
    "overview",
  );
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const selectedId = readySources.some(
    (source) => source.id === selectedSourceId,
  )
    ? selectedSourceId
    : (readySources[0]?.id ?? null);
  const detail = useQuery({
    queryKey: ["knowledge", projectId, "source", selectedId, "graph"],
    queryFn: ({ signal }) =>
      createKnowledgeClient({ projectId }).getSource(selectedId!, signal),
    enabled: selectedId !== null && graphView === "published",
    retry: false,
  });
  const revisionId = detail.data?.documents.items.find(
    (document) => document.status === "ready",
  )?.revisionId;
  const active = useActiveGraph(projectId, revisionId ? [revisionId] : []);
  const snapshotId = active.data?.items[0]?.snapshotId ?? null;
  const graph = useGraphSearch(projectId, snapshotId, "*");
  const selectedSource = readySources.find(
    (source) => source.id === selectedId,
  );
  const published =
    selectedSource && graph.data
      ? publishedGraphData(graph.data, selectedSource)
      : null;

  return (
    <div className="tap-project-graph">
      {readySources.length > 0 ? (
        <div className="tap-project-graph-controls">
          <div
            className="tap-project-graph-view"
            role="group"
            aria-label={locale === "zh" ? "图谱视图" : "Graph view"}
          >
            <button
              type="button"
              aria-pressed={graphView === "overview"}
              onClick={() => setGraphView("overview")}
            >
              {locale === "zh" ? "领域总览" : "Domain overview"}
            </button>
            <button
              type="button"
              aria-pressed={graphView === "published"}
              onClick={() => setGraphView("published")}
            >
              {locale === "zh" ? "已发布来源图谱" : "Published source graph"}
            </button>
          </div>
          {graphView === "published" ? (
            <label className="tap-project-graph-source">
              <span>{locale === "zh" ? "图谱来源" : "Graph source"}</span>
              <select
                value={selectedId ?? ""}
                onChange={(event) => setSelectedSourceId(event.target.value)}
              >
                {readySources.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
      ) : null}
      {selectedId === null ? (
        <p role="status">
          {locale === "zh"
            ? "请选择已就绪来源查看图谱。"
            : "Select a ready source to view its graph."}
        </p>
      ) : graphView === "overview" ? (
        <KnowledgeGraph
          copy={copy}
          query={query}
          sources={readySources}
          onViewSource={onViewSource}
        />
      ) : detail.isError ? (
        <p role="alert">
          {locale === "zh"
            ? "无法加载图谱来源，请重试。"
            : "The graph source could not be loaded. Try again."}
        </p>
      ) : detail.isPending ? (
        <p role="status">
          {locale === "zh" ? "正在加载图谱来源…" : "Loading graph source…"}
        </p>
      ) : active.isError || graph.isError ? (
        <p role="alert">
          {locale === "zh"
            ? "已发布图谱暂时无法加载。"
            : "The published graph is temporarily unavailable."}
        </p>
      ) : active.isPending || (snapshotId && graph.isPending) ? (
        <p role="status">
          {locale === "zh"
            ? "正在加载已发布图谱…"
            : "Loading the published graph…"}
        </p>
      ) : published ? (
        <KnowledgeGraph
          copy={copy}
          query={query}
          sources={selectedSource ? [selectedSource] : []}
          onViewSource={onViewSource}
          publishedData={published}
          publishedCaption={
            locale === "zh"
              ? "已发布的来源图谱 · 节点与关系来自服务，布局沿用已确认的原型。"
              : "Published source graph · nodes and relationships come from the service, arranged in the established prototype layout."
          }
        />
      ) : (
        <p role="status">
          {locale === "zh"
            ? "此来源尚无已发布图谱。"
            : "No published graph is ready for this source yet."}
        </p>
      )}
    </div>
  );
}

export function LibraryWorkspace({
  copy,
  onAddSource,
  onInspectSource,
  sources,
  loadState = "loaded",
  onReload,
  graphProjectId,
  locale = "en",
}: LibraryWorkspaceProps) {
  const [uploadPending, setUploadPending] = useState(false);
  const [uploadFailed, setUploadFailed] = useState(false);
  const [view, setView] = useState<"list" | "cards">("cards");
  const [mode, setMode] = useState<LibraryMode>(() =>
    graphProjectId === undefined ? "graph" : "list",
  );
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
    if (event.key === "Home") return selectMode("graph");
    if (event.key === "End") return selectMode("list");
    selectMode(currentMode === "list" ? "graph" : "list");
  };

  const openAddDialog = (event: MouseEvent<HTMLElement>) => {
    addDialogTriggerRef.current = event.currentTarget;
    setSelectedFile(null);
    setUploadFailed(false);
    setAddDialogOpen(true);
  };

  const closeAddDialog = () => {
    if (uploadPending) return;
    setSelectedFile(null);
    setAddDialogOpen(false);
  };

  const addSource = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (selectedFile === null || onAddSource === undefined || uploadPending)
      return;
    setUploadPending(true);
    setUploadFailed(false);
    try {
      await onAddSource(selectedFile);
      setSelectedFile(null);
      setAddDialogOpen(false);
    } catch {
      setUploadFailed(true);
    } finally {
      setUploadPending(false);
    }
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
            disabled={onAddSource === undefined}
            onClick={openAddDialog}
          >
            {copy.library.addSource}
          </Button>
        </div>
      </header>

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
              {loadState === "loading" ? (
                <p role="status" aria-label={copy.sources.loading}>
                  {copy.sources.loading}
                </p>
              ) : loadState === "error" ? (
                <div role="alert">
                  <p>{copy.sources.error}</p>
                  <Button onClick={onReload}>{copy.sources.retry}</Button>
                </div>
              ) : visibleSources.length === 0 ? (
                <div className="tap-catalog-empty">
                  {sources.length === 0
                    ? copy.sources.empty
                    : copy.library.noResults}
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
                        {onInspectSource !== undefined ? (
                          <button
                            type="button"
                            onClick={(event) =>
                              onInspectSource(source.id, event.currentTarget)
                            }
                            aria-label={`${copy.sources.view} ${source.name}`}
                          >
                            {copy.sources.view}
                          </button>
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
          {graphProjectId === undefined ? (
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
            <ProjectKnowledgeGraph
              projectId={graphProjectId}
              sources={visibleSources}
              locale={locale}
              copy={copy}
              query={query}
              onViewSource={(source) => {
                setQuery(source.name);
                setMode("list");
                listTabRef.current?.focus();
              }}
            />
          )}
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
                disabled={uploadPending}
                aria-label={copy.library.sourceFile}
                accept=".pdf,.docx,.md,.txt"
                onChange={(event) =>
                  setSelectedFile(event.target.files?.item(0) ?? null)
                }
              />
            </label>
            {uploadFailed ? <p role="alert">{copy.library.failed}</p> : null}
            <div className="tap-dialog-actions">
              <Button disabled={uploadPending} onClick={closeAddDialog}>
                {copy.library.cancel}
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                aria-label={copy.library.addSource}
                loading={uploadPending}
                disabled={
                  selectedFile === null ||
                  onAddSource === undefined ||
                  uploadPending
                }
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
