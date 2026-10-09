import { CloseOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

// `import type` only — see the note in `CommunityList.tsx`: type-only
// imports from `widgets/` are not flagged by dependency-cruiser's
// `features-do-not-import-upward` rule, only value imports.
import type { WorkspaceCopy } from "../../../widgets/tap/workspace/copy";

import { GraphVersionConflictError } from "../api/client";
import { useGraphNode } from "../api/queries";
import type { GraphNodeRelation, GraphNodeSource } from "../model/graph";
import { OTHER_COMMUNITY_COLOR } from "../model/palette";
import { nodeTypeLabel } from "./nodeTypeLabel";
import { relationTypeLabel } from "./relationTypeLabel";

/** Relation items shown per group before a "Show all" expansion. */
export const RELATION_GROUP_PREVIEW = 10;

export interface NodeDetailPanelProps {
  projectId: string;
  graphVersion: number;
  nodeId: string;
  /**
   * The palette color/community label the overview already computed for
   * this node. Omitted when the node was reached by following a relation
   * link to a node the canvas isn't currently drawing (outside the
   * community filter or current page) — the panel then falls back to
   * `GET /node`'s own `community` and a neutral color.
   */
  color?: string;
  communityLabel?: string;
  copy: WorkspaceCopy;
  locale: "en" | "zh";
  onClose: () => void;
  onAskAboutNode?: (label: string, sourceIds: string[]) => void;
  onOpenSource?: (sourceId: string, trigger: HTMLElement) => void;
  /**
   * Whether `sourceId` can actually be resolved to a real source id (see
   * `GraphOverview`'s `sourceIdByRevisionId`). When this returns `false` (or
   * is omitted and `onOpenSource` is undefined), "Open original" is hidden
   * for that source rather than rendered as a button that silently no-ops.
   */
  canOpenSource?: (sourceId: string) => boolean;
  onSelectNode: (nodeId: string) => void;
  /**
   * Whether the project's `GET /project` refetch triggered by
   * `useGraphVersionGuard` has settled (resolved or failed) since the
   * current version conflict began. While it is still in flight, a stale
   * `graphVersion` 409 is shown as loading (the panel is waiting for the
   * fresh version so the query above can re-key and retry on its own). If
   * the refetch has already settled and the conflict persists — e.g. the
   * project graph actually did not change version — treating it as
   * perpetual loading would leave the panel stuck forever, so it falls
   * through to the normal error body with a working retry button instead.
   * Defaults to `false` (not yet settled) so a bare conflict still reads as
   * loading, matching the pre-existing behavior.
   */
  projectRefetchSettled?: boolean;
}

/**
 * The relation group header is the mode (most frequent) `relationLabel`
 * among the group's edges, followed by the localized `relationType` label
 * (`relationTypeLabel` — never the raw code, so zh never surfaces one) —
 * the mode is used (rather than, say, the first edge's label) because a
 * single `relationType` can carry minor label variants across extractions,
 * and the most common phrasing is the most representative heading.
 */
function modeRelationLabel(edges: GraphNodeRelation["edges"]): string {
  const counts = new Map<string, number>();
  for (const edge of edges) {
    counts.set(edge.relationLabel, (counts.get(edge.relationLabel) ?? 0) + 1);
  }
  let best = edges[0]?.relationLabel ?? "";
  let bestCount = 0;
  for (const [label, count] of counts) {
    if (count > bestCount) {
      best = label;
      bestCount = count;
    }
  }
  return best;
}

function RelationGroup({
  group,
  nodeId,
  neighborLabelById,
  onSelectNode,
  copy,
}: {
  group: GraphNodeRelation;
  nodeId: string;
  neighborLabelById: ReadonlyMap<string, string>;
  onSelectNode: (nodeId: string) => void;
  copy: WorkspaceCopy;
}) {
  const [expanded, setExpanded] = useState(false);
  const wasExpandedRef = useRef(false);
  const listRef = useRef<HTMLUListElement>(null);
  const libraryCopy = copy.library;
  const headingId = `tap-relation-group-heading-${group.relationType}`;
  const visibleEdges = expanded
    ? group.edges
    : group.edges.slice(0, RELATION_GROUP_PREVIEW);

  // After "Show all" reveals the rest of the group, move focus to the
  // first newly-shown item (rather than leaving it on the now-removed
  // "Show all" button) — the ref only fires on the preview->expanded
  // transition, never on the initial (already-expanded) mount.
  useEffect(() => {
    if (expanded && !wasExpandedRef.current) {
      const buttons = listRef.current?.querySelectorAll("button") ?? [];
      const firstNewButton = buttons[RELATION_GROUP_PREVIEW] as
        HTMLElement | undefined;
      firstNewButton?.focus();
    }
    wasExpandedRef.current = expanded;
  }, [expanded]);

  return (
    <div className="tap-graph-inspector-relation-group">
      <h4 id={headingId}>
        {modeRelationLabel(group.edges)} (
        {relationTypeLabel(copy, group.relationType)})
      </h4>
      <p>{libraryCopy.relationCount(group.edges.length)}</p>
      <ul className="tap-graph-inspector-relations" ref={listRef}>
        {visibleEdges.map((edge) => {
          const isOutgoing = edge.sourceNodeId === nodeId;
          const otherNodeId = isOutgoing
            ? edge.targetNodeId
            : edge.sourceNodeId;
          const otherLabel = neighborLabelById.get(otherNodeId) ?? otherNodeId;
          return (
            <li key={edge.edgeId}>
              <button type="button" onClick={() => onSelectNode(otherNodeId)}>
                <span>{edge.relationLabel}</span>
                <em>
                  {isOutgoing
                    ? libraryCopy.relationOutgoing
                    : libraryCopy.relationIncoming}
                </em>
                <strong>{otherLabel}</strong>
              </button>
            </li>
          );
        })}
      </ul>
      {!expanded && group.edges.length > RELATION_GROUP_PREVIEW ? (
        <button
          type="button"
          aria-describedby={headingId}
          onClick={() => setExpanded(true)}
        >
          {libraryCopy.showAllRelations(group.edges.length)}
        </button>
      ) : null}
    </div>
  );
}

function SourceGroup({
  source,
  copy,
  onOpenSource,
  canOpenSource,
}: {
  source: GraphNodeSource;
  copy: WorkspaceCopy;
  onOpenSource?: (sourceId: string, trigger: HTMLElement) => void;
  canOpenSource?: (sourceId: string) => boolean;
}) {
  const libraryCopy = copy.library;
  const headingId = `tap-source-group-heading-${source.sourceRevisionId}`;
  const canOpen =
    onOpenSource !== undefined &&
    (canOpenSource === undefined || canOpenSource(source.sourceRevisionId));
  const openOriginal = (event: { currentTarget: HTMLElement }) =>
    onOpenSource?.(source.sourceRevisionId, event.currentTarget);
  const evidence = source.evidence ?? [];
  return (
    <div className="tap-graph-inspector-source-group">
      <h5 id={headingId}>{source.sourceName ?? source.sourceRevisionId}</h5>
      {evidence.length > 0 ? (
        <div>
          {evidence.map((item) => (
            <div key={item.chunkId}>
              {item.snippet !== null && item.snippet !== undefined ? (
                <p>{item.snippet}</p>
              ) : null}
              {canOpen ? (
                <button
                  type="button"
                  aria-describedby={headingId}
                  onClick={openOriginal}
                >
                  {libraryCopy.openOriginal}
                </button>
              ) : null}
            </div>
          ))}
        </div>
      ) : canOpen ? (
        <button
          type="button"
          aria-describedby={headingId}
          onClick={openOriginal}
        >
          {libraryCopy.openOriginal}
        </button>
      ) : null}
    </div>
  );
}

export function NodeDetailPanel({
  projectId,
  graphVersion,
  nodeId,
  color,
  communityLabel,
  copy,
  onClose,
  onAskAboutNode,
  onOpenSource,
  canOpenSource,
  onSelectNode,
  projectRefetchSettled = false,
}: NodeDetailPanelProps) {
  const libraryCopy = copy.library;
  const nodeDetailQuery = useGraphNode(projectId, graphVersion, nodeId);
  const detail = nodeDetailQuery.data;
  // A stale-version 409 is handled by `GraphOverview`'s
  // `useGraphVersionGuard` (it refetches `GET /project` and every query
  // re-keys once the fresh version is known) — treat it as still loading
  // rather than a dead-end error so the panel just waits for that refetch.
  // But if that refetch has already settled and the conflict is still
  // here (e.g. the project graph's version genuinely did not change),
  // waiting forever would leave the panel stuck on "loading" — fall
  // through to the normal error body instead.
  const isVersionConflict =
    nodeDetailQuery.error instanceof GraphVersionConflictError;
  const isPendingVersionConflict = isVersionConflict && !projectRefetchSettled;

  let body: ReactNode;
  if (nodeDetailQuery.isPending || isPendingVersionConflict) {
    body = <p role="status">{libraryCopy.nodeDetailsLoading}</p>;
  } else if (nodeDetailQuery.isError || detail === undefined) {
    body = (
      <>
        <p role="alert">{libraryCopy.nodeDetailsError}</p>
        <button type="button" onClick={() => void nodeDetailQuery.refetch()}>
          {copy.sources.retry}
        </button>
      </>
    );
  } else {
    const node = detail.node;
    const sources = detail.sources ?? [];
    const relations = detail.relations ?? [];
    const neighbors = detail.neighbors ?? [];
    const neighborLabelById = new Map(
      neighbors.map((neighbor) => [neighbor.nodeId, neighbor.label]),
    );
    const aliases = node.aliases ?? [];
    const totalConnections = relations.reduce(
      (total, group) => total + group.edges.length,
      0,
    );
    const effectiveColor = color ?? OTHER_COMMUNITY_COLOR;
    const effectiveCommunityLabel =
      communityLabel ?? detail.community?.label ?? libraryCopy.otherCommunity;

    body = (
      <>
        <div className="tap-graph-inspector-title">
          <span
            style={{ "--tap-community-color": effectiveColor } as CSSProperties}
            aria-hidden="true"
          />
          <div>
            <small>{nodeTypeLabel(copy, node.nodeType)}</small>
            <h3>{node.label}</h3>
          </div>
        </div>
        <dl>
          <div>
            <dt>{libraryCopy.community}</dt>
            <dd>{effectiveCommunityLabel}</dd>
          </div>
          <div>
            <dt>{libraryCopy.relationships}</dt>
            <dd>
              {totalConnections} {libraryCopy.connections}
            </dd>
          </div>
        </dl>
        {aliases.length > 0 ? (
          <div>
            <h4>{libraryCopy.aliases}</h4>
            <p>{aliases.join(", ")}</p>
          </div>
        ) : null}
        {onAskAboutNode !== undefined ? (
          <button
            type="button"
            onClick={() =>
              onAskAboutNode(
                node.label,
                sources.map((source) => source.sourceRevisionId),
              )
            }
          >
            {libraryCopy.askAboutNode}
          </button>
        ) : null}
        {sources.length > 0 ? (
          <div>
            <h4>{libraryCopy.nodeSources}</h4>
            <div>
              {sources.map((source) => (
                <p key={source.sourceRevisionId}>
                  {source.sourceName ?? source.sourceRevisionId}
                </p>
              ))}
            </div>
          </div>
        ) : null}
        {relations.length > 0 ? (
          <div className="tap-graph-inspector-relation-groups">
            {relations.map((group) => (
              <RelationGroup
                key={group.relationType}
                group={group}
                nodeId={nodeId}
                neighborLabelById={neighborLabelById}
                onSelectNode={onSelectNode}
                copy={copy}
              />
            ))}
          </div>
        ) : null}
        {sources.length > 0 ? (
          <div>
            <h4>{libraryCopy.evidenceSnippets}</h4>
            {sources.map((source) => (
              <SourceGroup
                key={source.sourceRevisionId}
                source={source}
                copy={copy}
                onOpenSource={onOpenSource}
                canOpenSource={canOpenSource}
              />
            ))}
          </div>
        ) : null}
      </>
    );
  }

  return (
    <>
      <header>
        <h2>{libraryCopy.nodeDetails}</h2>
        <button
          type="button"
          aria-label={libraryCopy.closeNodeDetails}
          onClick={onClose}
        >
          <CloseOutlined aria-hidden="true" />
        </button>
      </header>
      {body}
    </>
  );
}
