import { UploadChunkPreview } from "../../../features/knowledge/components/UploadChunkPreview";
import {
  DEFAULT_CHUNK_SETTINGS,
  type ChunkSettings,
} from "../../../features/knowledge/api/chunks";
import {
  AppstoreOutlined,
  BarsOutlined,
  DownloadOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { Button, Input, Segmented } from "antd";
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
import type { WorkspaceCopy } from "./copy";
import { KnowledgeGraph } from "./KnowledgeGraph";
import {
  useActiveGraph,
  useGraphOverview,
  useGraphSearch,
} from "../../../features/graph/api/queries";
import { overviewGraphData } from "./overviewGraphData";
import { publishedGraphData } from "./publishedGraphData";
import { createKnowledgeClient } from "../../../features/knowledge/api/client";
import type { LibrarySource } from "./model";

type LibraryMode = "list" | "graph";
type LibraryStatusFilter = "all" | LibrarySource["status"];

interface LibraryWorkspaceProps {
  copy: WorkspaceCopy;
  onAddSource?: (file: File, settings?: ChunkSettings) => Promise<void> | void;
  onInspectSource?: (sourceId: string, opener: HTMLElement) => void;
  sources: readonly LibrarySource[];
  loadState?: "loading" | "loaded" | "error";
  onReload?: () => void;
  graphProjectId?: string;
  locale?: "en" | "zh";
}

type GraphView = "overview" | "source";

function ProjectKnowledgeGraph({
  projectId,
  sources,
  allSources,
  locale,
  copy,
  query,
  onViewSource,
}: {
  projectId: string;
  sources: readonly LibrarySource[];
  allSources: readonly LibrarySource[];
  locale: "en" | "zh";
  copy: WorkspaceCopy;
  query: string;
  onViewSource: (source: LibrarySource) => void;
}) {
  const [graphView, setGraphView] = useState<GraphView>("overview");
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [focus, setFocus] = useState<{
    sourceId: string;
    nodeId: string;
    key: number;
  } | null>(null);
  const overview = useGraphOverview(projectId, graphView === "overview");
  const visibleReady = sources.filter((source) => source.status === "ready");
  const pinned = allSources.find(
    (source) =>
      source.id === selectedSourceId &&
      source.status === "ready" &&
      !visibleReady.includes(source),
  );
  const readySources = pinned ? [pinned, ...visibleReady] : visibleReady;
  const sourceNames = new Map(
    allSources.map((source) => [source.id, source.name]),
  );
  const overviewData = useMemo(
    () => (overview.data ? overviewGraphData(overview.data, allSources) : null),
    [overview.data, allSources],
  );

  const showMember = (sourceId: string, nodeId: string) => {
    setSelectedSourceId(sourceId);
    setFocus((current) => ({
      sourceId,
      nodeId: `published-${nodeId}`,
      key: (current?.key ?? 0) + 1,
    }));
    setGraphView("source");
  };

  const memberDetail = (overviewNodeId: string) => {
    const node = overview.data?.nodes.find(
      (item) => item.overviewNodeId === overviewNodeId,
    );
    const members = (node?.members ?? []).filter(
      (member, index, all) =>
        sourceNames.has(member.sourceId) &&
        all.findIndex((item) => item.sourceId === member.sourceId) === index,
    );
    return (
      <div className="tap-graph-source-detail">
        <p id="tap-graph-member-sources">{copy.library.graphMemberSources}</p>
        <ul
          className="tap-graph-member-sources"
          aria-labelledby="tap-graph-member-sources"
        >
          {members.map((member) => (
            <li key={member.sourceId}>
              <button
                type="button"
                onClick={() => showMember(member.sourceId, member.nodeId)}
              >
                {sourceNames.get(member.sourceId)}
              </button>
            </li>
          ))}
        </ul>
      </div>
    );
  };

  return (
    <div className="tap-project-graph">
      <div className="tap-project-graph-controls">
        <Segmented
          aria-label={copy.library.graphViewLabel}
          value={graphView}
          onChange={(value) => setGraphView(value as GraphView)}
          options={[
            { label: copy.library.graphDomain, value: "overview" },
            { label: copy.library.graphPublished, value: "source" },
          ]}
        />
      </div>
      {graphView === "overview" ? (
        overview.isError ? (
          <p role="alert">{copy.library.graphOverviewFailed}</p>
        ) : overview.isPending ? (
          <p role="status">{copy.library.graphOverviewLoading}</p>
        ) : overviewData && overviewData.nodes.length > 0 ? (
          <KnowledgeGraph
            copy={copy}
            query={query}
            sources={[]}
            onViewSource={onViewSource}
            publishedData={overviewData}
            publishedCaption={copy.library.graphOverviewCaption.replace(
              "{count}",
              String(overview.data?.stats.sourceCount ?? 0),
            )}
            nodeDetail={memberDetail}
          />
        ) : (
          <p role="status">{copy.library.graphOverviewEmpty}</p>
        )
      ) : (
        <SourceKnowledgeGraph
          projectId={projectId}
          readySources={readySources}
          selectedSourceId={selectedSourceId}
          onSelectSource={(sourceId) => {
            setSelectedSourceId(sourceId);
            setFocus(null);
          }}
          focus={focus}
          locale={locale}
          copy={copy}
          query={query}
          onViewSource={onViewSource}
        />
      )}
    </div>
  );
}

function SourceKnowledgeGraph({
  projectId,
  readySources,
  selectedSourceId,
  onSelectSource,
  focus,
  locale,
  copy,
  query,
  onViewSource,
}: {
  projectId: string;
  readySources: readonly LibrarySource[];
  selectedSourceId: string | null;
  onSelectSource: (sourceId: string) => void;
  focus: { sourceId: string; nodeId: string; key: number } | null;
  locale: "en" | "zh";
  copy: WorkspaceCopy;
  query: string;
  onViewSource: (source: LibrarySource) => void;
}) {
  const selectedId = readySources.some(
    (source) => source.id === selectedSourceId,
  )
    ? selectedSourceId
    : (readySources[0]?.id ?? null);
  const detail = useQuery({
    queryKey: ["knowledge", projectId, "source", selectedId, "graph"],
    queryFn: ({ signal }) =>
      createKnowledgeClient({ projectId }).getSource(selectedId!, signal),
    enabled: selectedId !== null,
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
  const focusedNodeId =
    focus !== null && focus.sourceId === selectedId ? focus.nodeId : null;

  return (
    <>
      {readySources.length > 0 ? (
        <div className="tap-project-graph-controls">
          <label className="tap-project-graph-source">
            <span>{locale === "zh" ? "图谱来源" : "Graph source"}</span>
            <select
              value={selectedId ?? ""}
              onChange={(event) => onSelectSource(event.target.value)}
            >
              {readySources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}
      {selectedId === null ? (
        <p role="status">
          {locale === "zh"
            ? "请选择已就绪来源查看图谱。"
            : "Select a ready source to view its graph."}
        </p>
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
          key={focusedNodeId === null ? "source" : `focus-${focus!.key}`}
          copy={copy}
          query={query}
          sources={selectedSource ? [selectedSource] : []}
          onViewSource={onViewSource}
          publishedData={published}
          publishedCaption={
            locale === "zh"
              ? "已发布的来源图谱 · 节点与关系来自服务。"
              : "Published source graph · nodes and relationships come from the service."
          }
          initialSelectedNodeId={focusedNodeId}
        />
      ) : (
        <p role="status">
          {locale === "zh"
            ? "此来源尚无已发布图谱。"
            : "No published graph is ready for this source yet."}
        </p>
      )}
    </>
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

  const [chunkSettings, setChunkSettings] = useState(DEFAULT_CHUNK_SETTINGS);
  const openAddDialog = (event: MouseEvent<HTMLElement>) => {
    addDialogTriggerRef.current = event.currentTarget;
    setSelectedFile(null);
    setChunkSettings(DEFAULT_CHUNK_SETTINGS);
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
      await onAddSource(
        selectedFile,
        graphProjectId ? chunkSettings : undefined,
      );
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
                          <span>{source.description}</span>
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
            <p role="status">
              {locale === "zh" ? "未选择项目。" : "No project is selected."}
            </p>
          ) : (
            <ProjectKnowledgeGraph
              projectId={graphProjectId}
              sources={visibleSources}
              allSources={sources}
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
                accept=".pdf,.docx,.xlsx,.md,.markdown,.txt"
                onChange={(event) =>
                  setSelectedFile(event.target.files?.item(0) ?? null)
                }
              />
            </label>
            {graphProjectId && selectedFile && (
              <UploadChunkPreview
                key={`${selectedFile.name}:${selectedFile.lastModified}`}
                projectId={graphProjectId}
                file={selectedFile}
                value={chunkSettings}
                onChange={setChunkSettings}
              />
            )}
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
