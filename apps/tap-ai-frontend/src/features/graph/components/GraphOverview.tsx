import { CloseOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useState } from "react";
import type { ComponentType, CSSProperties } from "react";

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
  useGraphNode,
  useGraphOverview,
  useGraphProject,
  useGraphSearch,
} from "../api/queries";
import { MAX_GRAPH_NODES, OVERVIEW_PAGE } from "../model/graph";
import { OTHER_COMMUNITY_ID, toOverviewData } from "./toOverviewData";

function nodeTypeLabel(copy: WorkspaceCopy, nodeType: string): string {
  return (
    copy.library.nodeTypes[nodeType as keyof typeof copy.library.nodeTypes] ??
    nodeType
  );
}

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
  query,
  sourceScope,
  publishedSources = [],
  onAskAboutNode,
  onOpenSource,
  Canvas,
}: {
  projectId: string;
  locale: "en" | "zh";
  copy: WorkspaceCopy;
  query: string;
  sourceScope: GraphSourceScope;
  publishedSources?: readonly PublishedSourceRevision[];
  onAskAboutNode?: (label: string, sourceIds: string[]) => void;
  onOpenSource?: (sourceId: string) => void;
  Canvas: ComponentType<KnowledgeGraphProps>;
}) {
  const libraryCopy = copy.library;

  const [selectedCommunities, setSelectedCommunities] =
    useState<ReadonlySet<string> | null>(null);
  const [nodeLimit, setNodeLimit] = useState(OVERVIEW_PAGE);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

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

  const nodeDetailQuery = useGraphNode(
    gatedProjectId,
    graphVersion,
    selectedNodeId,
  );

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

  const visibleNodeIds = useMemo(
    () =>
      new Set(
        overview.nodes
          .filter((node) => effectiveSelectedCommunities.has(node.community))
          .map((node) => node.id),
      ),
    [overview.nodes, effectiveSelectedCommunities],
  );

  useEffect(() => {
    if (selectedNodeId !== null && !visibleNodeIds.has(selectedNodeId)) {
      setSelectedNodeId(null);
    }
  }, [selectedNodeId, visibleNodeIds]);

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
  const nodeDetail = nodeDetailQuery.data;
  const nodeDetailSourceIds = (nodeDetail?.sources ?? [])
    .map((source) => sourceIdByRevisionId.get(source.sourceRevisionId))
    .filter((id): id is string => id !== undefined);

  const selectedNode =
    selectedNodeId !== null
      ? (overview.nodes.find((node) => node.id === selectedNodeId) ?? null)
      : null;

  const detailPanel = (
    <aside
      className="tap-graph-inspector"
      hidden={selectedNode === null}
      role="region"
      aria-label={libraryCopy.nodeDetails}
    >
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
      {selectedNode === null ? (
        <p className="tap-graph-inspector-empty">{libraryCopy.selectNode}</p>
      ) : (
        <>
          <div className="tap-graph-inspector-title">
            <span
              style={
                { "--tap-community-color": selectedNode.color } as CSSProperties
              }
              aria-hidden="true"
            />
            <div>
              <small>{nodeTypeLabel(copy, selectedNode.nodeType)}</small>
              <h3>{selectedNode.label}</h3>
            </div>
          </div>
          <dl>
            <div>
              <dt>{libraryCopy.community}</dt>
              <dd>{selectedNode.communityLabel}</dd>
            </div>
            <div>
              <dt>{libraryCopy.relationships}</dt>
              <dd>
                {
                  overview.edges.filter(
                    (edge) =>
                      edge.source === selectedNode.id ||
                      edge.target === selectedNode.id,
                  ).length
                }{" "}
                {libraryCopy.connections}
              </dd>
            </div>
          </dl>
          {selectedNode.aliases.length > 0 ? (
            <p>{selectedNode.aliases.join(", ")}</p>
          ) : null}
          <ul className="tap-graph-inspector-relations">
            {overview.edges
              .filter(
                (edge) =>
                  edge.source === selectedNode.id ||
                  edge.target === selectedNode.id,
              )
              .map((edge) => {
                const otherId =
                  edge.source === selectedNode.id ? edge.target : edge.source;
                const otherNode = overview.nodes.find(
                  (node) => node.id === otherId,
                );
                return (
                  <li key={edge.id}>
                    <span>{edge.label}</span>
                    <strong>{otherNode?.label ?? otherId}</strong>
                  </li>
                );
              })}
          </ul>
          {onAskAboutNode ? (
            <button
              type="button"
              onClick={() =>
                onAskAboutNode(selectedNode.label, nodeDetailSourceIds)
              }
            >
              {copy.chat.messageTapper}
            </button>
          ) : null}
          {onOpenSource
            ? (nodeDetail?.sources ?? []).map((source) => {
                const sourceId = sourceIdByRevisionId.get(
                  source.sourceRevisionId,
                );
                if (sourceId === undefined) return null;
                return (
                  <button
                    key={source.sourceRevisionId}
                    type="button"
                    onClick={() => onOpenSource(sourceId)}
                  >
                    {libraryCopy.viewSource}
                  </button>
                );
              })
            : null}
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

  return (
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
  );
}
