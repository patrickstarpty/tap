import { CloseOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType } from "react";

// `import type` only — see the note in `CommunityList.tsx`: type-only
// imports from `widgets/` are not flagged by dependency-cruiser's
// `features-do-not-import-upward` rule, only value imports. This module
// never imports a `widgets/` *value*; the canvas itself is injected by the
// caller (see `Canvas` below) so this module never needs to import
// `widgets/tap/workspace/KnowledgeGraph.tsx` directly.
import type { WorkspaceCopy } from "../../../widgets/tap/workspace/copy";
import type { KnowledgeGraphProps } from "../../../widgets/tap/workspace/KnowledgeGraph";
import type { PublishedSourceRevision } from "../../../widgets/tap/workspace/LibraryWorkspace";

import { useGraphLayout } from "../model/layout";
import {
  useGraphHighlight,
  useGraphNode,
  useGraphOverview,
  useGraphProject,
  useGraphSearch,
  useGraphVersionGuard,
} from "../api/queries";
import { MAX_GRAPH_NODES, OVERVIEW_PAGE } from "../model/graph";
import type { GraphHighlightState } from "../model/highlight";
import { NodeDetailPanel } from "./NodeDetailPanel";
import { OTHER_COMMUNITY_ID, toOverviewData } from "./toOverviewData";

const EMPTY_EDGE_IDS: readonly string[] = [];

/**
 * Describes whether `sourceRevisionIds` is ready to scope a graph query.
 * The backend treats an empty `sourceRevisionIds` array as "no filter" (the
 * whole project graph), so the caller (`LibraryWorkspace`) must distinguish
 * "filters narrowed to zero published sources" (`no-match`) and "the
 * published-source list hasn't loaded yet" (`loading`) from a genuinely
 * empty filter — only `ready` may be sent to the graph queries.
 */
export type GraphSourceScope =
  | { status: "ready"; sourceRevisionIds: readonly string[] }
  | { status: "loading" }
  | { status: "no-match" }
  | { status: "unavailable" };

export function GraphOverview({
  projectId,
  copy,
  locale,
  query,
  sourceScope,
  publishedSources = [],
  onAskAboutNode,
  onOpenSource,
  highlight = null,
  onClearHighlight,
  Canvas,
}: {
  projectId: string;
  locale: "en" | "zh";
  copy: WorkspaceCopy;
  query: string;
  sourceScope: GraphSourceScope;
  publishedSources?: readonly PublishedSourceRevision[];
  onAskAboutNode?: (label: string, sourceIds: string[]) => void;
  onOpenSource?: (sourceId: string, trigger: HTMLElement) => void;
  /**
   * A cited-path highlight carried in through `window.history.state` (see
   * `features/graph/model/highlight.ts`), e.g. from "View in Library" on an
   * answer's edge citation. `null` when the Library graph is viewed on its
   * own, outside any highlight navigation.
   */
  highlight?: GraphHighlightState | null;
  onClearHighlight?: () => void;
  Canvas: ComponentType<KnowledgeGraphProps>;
}) {
  const libraryCopy = copy.library;

  const [selectedCommunities, setSelectedCommunities] =
    useState<ReadonlySet<string> | null>(null);
  const [nodeLimit, setNodeLimit] = useState(OVERVIEW_PAGE);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  // Wraps `<Canvas>` (see the returned JSX below) so a version-change panel
  // close can return focus there instead of <body>.
  const canvasRegionRef = useRef<HTMLDivElement>(null);
  // The node detail panel's own container (`detailPanel` below) — read at
  // the moment a version change closes the panel, to tell whether focus
  // actually needs rescuing (see the `graphVersion` effect further down).
  const detailPanelRef = useRef<HTMLElement>(null);

  const trimmedQuery = query.trim();
  const normalizedQuery = trimmedQuery.toLocaleLowerCase();
  const sourceRevisionIds =
    sourceScope.status === "ready" ? sourceScope.sourceRevisionIds : [];
  const sortedSourceRevisionIds = useMemo(
    () => [...sourceRevisionIds].sort(),
    [sourceRevisionIds],
  );

  const projectQuery = useGraphProject(projectId);
  const project = projectQuery.data ?? null;
  const graphVersion = project?.graphVersion ?? null;
  // Gate the overview/search/node queries behind the project query (so the
  // overview is not fetched before the project's graphVersion is known,
  // which would otherwise double-fetch once the version resolves) and
  // behind the source scope being resolved (an empty `sourceRevisionIds`
  // array means "no filter" server-side, so it must never be sent while
  // the real filtered set is still loading or resolved to zero matches).
  const gatedProjectId =
    projectQuery.isSuccess && sourceScope.status === "ready" ? projectId : null;

  const communities = project?.communities ?? [];
  const communitiesBySize = useMemo(
    () => [...communities].sort((a, b) => b.size - a.size),
    [communities],
  );
  const realCommunityIds = useMemo(
    () => communitiesBySize.map((community) => community.communityId),
    [communitiesBySize],
  );
  const effectiveSelectedCommunities = useMemo(
    () =>
      selectedCommunities ?? new Set([...realCommunityIds, OTHER_COMMUNITY_ID]),
    [selectedCommunities, realCommunityIds],
  );
  const communityIdsForRequest = useMemo(() => {
    const allRealSelected = realCommunityIds.every((id) =>
      effectiveSelectedCommunities.has(id),
    );
    return allRealSelected
      ? []
      : realCommunityIds.filter((id) => effectiveSelectedCommunities.has(id));
  }, [realCommunityIds, effectiveSelectedCommunities]);

  const overviewQuery = useGraphOverview(gatedProjectId, graphVersion, {
    sourceRevisionIds: sortedSourceRevisionIds,
    communityIds: communityIdsForRequest,
    nodeLimit,
  });
  const searchQuery = useGraphSearch(
    gatedProjectId,
    graphVersion,
    trimmedQuery,
    sortedSourceRevisionIds,
  );
  const subgraph =
    trimmedQuery.length > 0 ? searchQuery.data : overviewQuery.data;

  // `NodeDetailPanel` (below) issues its own `GET /node` fetch for
  // `selectedNodeId`, but that query's error is only visible to the
  // version guard if something in *this* component also reads it — call
  // the same hook here (same query key, so react-query serves/shares one
  // request) purely to surface its error into `useGraphVersionGuard`.
  const nodeQuery = useGraphNode(gatedProjectId, graphVersion, selectedNodeId);

  // A 409 on any of these queries means the graph was re-merged mid-session
  // (a stale `graphVersion`) — without this guard, that 409 would otherwise
  // be a dead end (see `graphUnavailable`/`nodeDetailsError`). The guard
  // invalidates every graph query and refetches `GET /project`; once that
  // resolves with the fresh version, every query below naturally re-keys
  // and retries on its own.
  useGraphVersionGuard(projectId, [
    projectQuery.error,
    overviewQuery.error,
    searchQuery.error,
    nodeQuery.error,
  ]);

  // Whether the project's `GET /project` has updated — successfully or
  // with its own error — *since* the node query's current conflict began.
  // `!projectQuery.isFetching` is not enough: it reads `true` ("settled")
  // on the very first render after the node 409s, before the version
  // guard's own refetch has even started, which would flash the error body
  // for one render before the guard kicks in. Comparing timestamps instead
  // means "settled" only becomes true once the project has actually
  // produced a result timed after the conflict — whether that result is a
  // genuine version bump (the panel closes anyway, see the `graphVersion`
  // effect above) or the same version repeated (the conflict is real and
  // stuck, so `NodeDetailPanel` should stop pretending to load).
  const nodeConflictAt = nodeQuery.errorUpdatedAt ?? 0;
  const projectUpdatedAt = Math.max(
    projectQuery.dataUpdatedAt ?? 0,
    projectQuery.errorUpdatedAt ?? 0,
  );
  const projectRefetchSettled = projectUpdatedAt > nodeConflictAt;

  // `POST /highlight` never pins a `graphVersion` (see `highlight.ts`'s
  // doc comment — an answer's cited edges can predate the project's
  // current graph version), so it never 409s on a stale version and is
  // deliberately left out of `useGraphVersionGuard` above.
  const highlightQuery = useGraphHighlight(
    highlight ? projectId : null,
    highlight?.edgeIds ?? EMPTY_EDGE_IDS,
  );
  const highlightResponseEdges = highlightQuery.data?.edges ?? [];
  // `POST /highlight` returns the requested edges *plus* their 1-hop graph
  // context (so the canvas can draw the neighborhood around a cited path,
  // not just the bare cited edges in isolation) — the context edges must
  // never be data-highlighted or listed in "Relations cited by the
  // answer", only the ones the answer actually cited. `citedHighlightEdges`
  // filters the response down to exactly that subset, in `highlight.edgeIds`
  // order (not the response's own order, which may interleave context
  // edges); both the `<ol>` below and `canvasHighlight` are built from it.
  const citedHighlightEdges = useMemo(() => {
    if (!highlight) return [];
    const edgeById = new Map(
      highlightResponseEdges.map((edge) => [edge.edgeId, edge]),
    );
    return highlight.edgeIds
      .map((edgeId) => edgeById.get(edgeId))
      .filter(
        (edge): edge is (typeof highlightResponseEdges)[number] =>
          edge !== undefined,
      );
  }, [highlight, highlightResponseEdges]);
  const highlightNodeLabelById = useMemo(
    () =>
      new Map(
        (highlightQuery.data?.nodes ?? []).map((node) => [
          node.nodeId,
          node.label,
        ]),
      ),
    [highlightQuery.data],
  );
  // A pending or failed `POST /highlight` must never be read as "every
  // requested edge is missing" — that would dim the whole canvas and show
  // the version-updated notice for a request that simply hasn't resolved
  // yet (or failed for an unrelated reason, e.g. `graph-job-busy`). Both
  // derived values below are therefore only computed once the request has
  // actually succeeded; `highlightStatus` drives the loading/error/success
  // branches in the "Highlighted path" region further down.
  //
  // `isFetching` only counts towards "pending" when combined with
  // `isError` — that's specifically clicking "Retry" on a query that's
  // already settled into `error` (react query keeps `status: "error"`,
  // and `isPending` false, for the whole duration of that retry; it only
  // updates once the new attempt itself resolves), so without `isError &&
  // isFetching` the retry would keep showing the stale error text instead
  // of a loading state while it's actually refetching. A query that has
  // already succeeded must *not* get the same treatment: react query also
  // sets `isFetching` during an ordinary background refetch of
  // already-good data, and reading that as "pending" would otherwise make
  // the canvas highlight disappear and a "Loading…" line flash on every
  // such background refetch, even though the last-known-good path is
  // still perfectly valid to show.
  const highlightStatus: "idle" | "pending" | "error" | "success" = !highlight
    ? "idle"
    : highlightQuery.isPending ||
        (highlightQuery.isError && highlightQuery.isFetching)
      ? "pending"
      : highlightQuery.isError
        ? "error"
        : "success";
  const missingHighlightEdgeIds = useMemo(() => {
    if (!highlight || highlightStatus !== "success") return [];
    const responseEdgeIds = new Set(
      highlightResponseEdges.map((edge) => edge.edgeId),
    );
    return highlight.edgeIds.filter((id) => !responseEdgeIds.has(id));
  }, [highlight, highlightStatus, highlightResponseEdges]);
  const canvasHighlight = useMemo(() => {
    if (!highlight || highlightStatus !== "success") return null;
    // Only the cited edges themselves are data-highlighted — a context
    // edge from the response's 1-hop neighborhood stays normal (dimmed)
    // context, same as any other edge the overview wasn't asked about.
    const nodeIds = new Set<string>();
    for (const edge of citedHighlightEdges) {
      nodeIds.add(edge.sourceNodeId);
      nodeIds.add(edge.targetNodeId);
    }
    return {
      edgeIds: new Set(citedHighlightEdges.map((edge) => edge.edgeId)),
      nodeIds,
    };
  }, [highlight, highlightStatus, citedHighlightEdges]);

  // Same size-descending order as the palette assignment in
  // `toOverviewData` so the force layout seeds communities in the same
  // visual order their colors are assigned.
  const communityOrderIds = realCommunityIds;
  const emptySubgraphNodes = useMemo(() => [], []);
  const emptySubgraphEdges = useMemo(() => [], []);
  const layoutNodes = subgraph?.nodes ?? emptySubgraphNodes;
  const layoutEdges = subgraph?.edges ?? emptySubgraphEdges;
  const positions = useGraphLayout(layoutNodes, layoutEdges, communityOrderIds);

  const otherLabel = libraryCopy.otherCommunity;
  const overview = useMemo(
    () =>
      subgraph
        ? toOverviewData(subgraph, communities, positions, otherLabel)
        : { nodes: [], edges: [], communityOrder: [] },
    [subgraph, communities, positions, otherLabel],
  );

  useEffect(() => {
    if (normalizedQuery.length === 0) setSelectedNodeId(null);
  }, [normalizedQuery]);

  // The node detail panel stays open for any selected node id, including a
  // relation neighbor that isn't drawn in the current (community-filtered
  // or paginated) overview — community toggles and pagination narrow what
  // the *canvas* draws, not which node is actually selected. The selection
  // is only cleared when the node it points at could truly have
  // disappeared: the graph was re-merged to a new version. When that
  // happens *and* the user's focus was actually inside the now-closing
  // panel, it would otherwise fall back to <body> once that focused
  // element unmounts — move it to the canvas region instead
  // (`canvasRegionRef`, below) so keyboard/screen-reader users land
  // somewhere sensible. Focus is left alone when no panel was open, or
  // when it was open but focus was elsewhere (e.g. the search box) — only
  // the element that's about to disappear needs rescuing. The
  // previous-version ref's initial `null` means the very first version
  // resolving (mount) never triggers this.
  const previousGraphVersionRef = useRef<number | null>(null);
  useEffect(() => {
    const previousGraphVersion = previousGraphVersionRef.current;
    previousGraphVersionRef.current = graphVersion;
    if (
      previousGraphVersion === null ||
      previousGraphVersion === graphVersion
    ) {
      return;
    }
    const focusWasInPanel =
      selectedNodeId !== null &&
      detailPanelRef.current !== null &&
      detailPanelRef.current.contains(document.activeElement);
    setSelectedNodeId(null);
    if (focusWasInPanel) {
      canvasRegionRef.current?.focus();
    }
  }, [graphVersion]);

  const toggleCommunity = (communityId: string) => {
    setSelectedCommunities((current) => {
      const base = current ?? effectiveSelectedCommunities;
      const next = new Set(base);
      if (next.has(communityId)) next.delete(communityId);
      else next.add(communityId);
      return next;
    });
  };
  const selectAllCommunities = (all: boolean) => {
    setSelectedCommunities(
      all ? new Set([...realCommunityIds, OTHER_COMMUNITY_ID]) : new Set(),
    );
  };

  const extractingCount = sourceRevisionIds.filter((id) =>
    (project?.extractingRevisionIds ?? []).includes(id),
  ).length;
  const partialCount = sourceRevisionIds.filter((id) =>
    (project?.partialRevisionIds ?? []).includes(id),
  ).length;
  const communitiesFooter = {
    extracting: extractingCount,
    partial: partialCount,
  };
  const communitiesNotice =
    project?.status === "MERGING" ? (
      <p role="status">{libraryCopy.graphMerging}</p>
    ) : null;

  const sourceIdByRevisionId = useMemo(
    () =>
      new Map(
        publishedSources.map((source) => [source.revisionId, source.sourceId]),
      ),
    [publishedSources],
  );

  // A node selected from the canvas is always drawn in `overview.nodes`,
  // so its palette color/community label come from there; a node selected
  // by navigating a relation link may not be (it can be outside the
  // community filter or page the canvas currently draws) — `NodeDetailPanel`
  // falls back to `GET /node`'s own `community` and a neutral color for
  // those (see `NodeDetailPanel`'s `color`/`communityLabel` fallbacks).
  const selectedNode =
    selectedNodeId !== null
      ? (overview.nodes.find((node) => node.id === selectedNodeId) ?? null)
      : null;

  // `NodeDetailPanel` only knows the `sourceRevisionId`s its own `GET
  // /node` fetch returns — it has no access to `publishedSources`, so this
  // is the one place those revision ids are resolved to the real source
  // ids `onAskAboutNode`/`onOpenSource` expect (see `sourceIdByRevisionId`
  // above). A revision with no matching published source (not yet
  // resolvable) is dropped rather than forwarded as a raw revision id, and
  // `canOpenSource` lets the panel hide "Open original" entirely for it
  // rather than rendering a button that silently no-ops on click.
  const detailPanel = (
    <aside
      ref={detailPanelRef}
      className="tap-graph-inspector"
      hidden={selectedNodeId === null}
      role="region"
      aria-label={libraryCopy.nodeDetails}
    >
      {selectedNodeId !== null && graphVersion !== null ? (
        <NodeDetailPanel
          key={selectedNodeId}
          projectId={projectId}
          graphVersion={graphVersion}
          nodeId={selectedNodeId}
          color={selectedNode?.color}
          communityLabel={selectedNode?.communityLabel}
          copy={copy}
          locale={locale}
          onClose={() => setSelectedNodeId(null)}
          onSelectNode={setSelectedNodeId}
          projectRefetchSettled={projectRefetchSettled}
          onAskAboutNode={
            onAskAboutNode
              ? (label, sourceRevisionIds) => {
                  const resolvedSourceIds = sourceRevisionIds
                    .map((revisionId) => sourceIdByRevisionId.get(revisionId))
                    .filter((id): id is string => id !== undefined);
                  onAskAboutNode(label, resolvedSourceIds);
                }
              : undefined
          }
          onOpenSource={
            onOpenSource
              ? (sourceRevisionId, trigger) => {
                  const sourceId = sourceIdByRevisionId.get(sourceRevisionId);
                  if (sourceId !== undefined) onOpenSource(sourceId, trigger);
                }
              : undefined
          }
          canOpenSource={(sourceRevisionId) =>
            sourceIdByRevisionId.has(sourceRevisionId)
          }
        />
      ) : (
        <>
          <header>
            <h2>{libraryCopy.nodeDetails}</h2>
            <button
              type="button"
              aria-label={libraryCopy.closeNodeDetails}
              onClick={() => setSelectedNodeId(null)}
            >
              <CloseOutlined aria-hidden="true" />
            </button>
          </header>
          <p className="tap-graph-inspector-empty">{libraryCopy.selectNode}</p>
        </>
      )}
    </aside>
  );

  if (projectQuery.isPending) {
    return <p role="status">{libraryCopy.graphLoading}</p>;
  }
  if (projectQuery.isError) {
    return <p role="alert">{libraryCopy.graphUnavailable}</p>;
  }
  if (sourceScope.status === "loading") {
    return <p role="status">{copy.sources.loading}</p>;
  }
  if (sourceScope.status === "unavailable") {
    return <p role="alert">{libraryCopy.graphUnavailable}</p>;
  }
  if (sourceScope.status === "no-match") {
    return <p role="status">{libraryCopy.noResults}</p>;
  }
  if (project === null || project.nodeCount === 0) {
    return (
      <div className="tap-graph-empty">
        <p>{libraryCopy.graphEmpty}</p>
        <p>{libraryCopy.graphEmptyHint}</p>
      </div>
    );
  }

  const atCap = overview.nodes.length >= MAX_GRAPH_NODES;
  const truncated = overview.nodes.length === nodeLimit && !atCap;

  // `highlightPathRegion` is a *sibling* of the canvas region below, not
  // nested inside it — both are `role="region"`, and a region nested
  // inside another region is confusing for assistive tech (which region is
  // the user actually in?). The outer `tap-graph-highlight-layout` wrapper
  // that holds both carries no role of its own, just the flex layout that
  // places this list beside the canvas.
  const highlightPathRegion = highlight ? (
    <section
      className="tap-graph-highlight-path"
      role="region"
      aria-label={libraryCopy.highlightedPath}
    >
      {highlightStatus === "pending" ? (
        <p role="status">{libraryCopy.highlightLoading}</p>
      ) : highlightStatus === "error" ? (
        <>
          <p role="alert">{libraryCopy.highlightError}</p>
          <button type="button" onClick={() => void highlightQuery.refetch()}>
            {copy.sources.retry}
          </button>
        </>
      ) : (
        <>
          <p>{libraryCopy.highlightCaption}</p>
          <ol>
            {citedHighlightEdges.map((edge) => {
              const sourceLabel =
                highlightNodeLabelById.get(edge.sourceNodeId) ??
                edge.sourceNodeId;
              const targetLabel =
                highlightNodeLabelById.get(edge.targetNodeId) ??
                edge.targetNodeId;
              const relationLabel = edge.relationLabel || edge.relationType;
              return (
                <li key={edge.edgeId}>
                  {sourceLabel} —{relationLabel}→ {targetLabel}
                </li>
              );
            })}
          </ol>
        </>
      )}
      {onClearHighlight ? (
        <button type="button" onClick={onClearHighlight}>
          {libraryCopy.clearHighlight}
        </button>
      ) : null}
    </section>
  ) : null;

  return (
    <div className="tap-graph-highlight-layout">
      <div
        ref={canvasRegionRef}
        tabIndex={-1}
        role="region"
        aria-label={libraryCopy.canvasRegionLabel}
        className="tap-graph-canvas-region"
      >
        {highlightStatus === "success" && missingHighlightEdgeIds.length > 0 ? (
          <p role="status">{libraryCopy.versionUpdated}</p>
        ) : null}
        <Canvas
          copy={copy}
          nodes={overview.nodes}
          edges={overview.edges}
          communities={overview.communityOrder}
          activeCommunities={effectiveSelectedCommunities}
          onToggleCommunity={toggleCommunity}
          onSelectAllCommunities={selectAllCommunities}
          communitiesFooter={communitiesFooter}
          communitiesNotice={communitiesNotice}
          searchQuery={query}
          selectedNodeId={selectedNodeId}
          onSelectNode={setSelectedNodeId}
          highlight={canvasHighlight}
          detailPanel={detailPanel}
          caption={libraryCopy.overviewCaption}
          onLoadMore={
            truncated
              ? () =>
                  setNodeLimit((current) =>
                    Math.min(MAX_GRAPH_NODES, current + OVERVIEW_PAGE),
                  )
              : undefined
          }
          loadMoreLabel={libraryCopy.loadMore}
        />
      </div>
      {highlightPathRegion}
    </div>
  );
}
