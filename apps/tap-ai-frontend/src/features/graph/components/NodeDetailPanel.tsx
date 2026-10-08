import { CloseOutlined } from "@ant-design/icons";
import { useState } from "react";
import type { CSSProperties } from "react";

// `import type` only — see the note in `CommunityList.tsx`: type-only
// imports from `widgets/` are not flagged by dependency-cruiser's
// `features-do-not-import-upward` rule, only value imports.
import type { WorkspaceCopy } from "../../../widgets/tap/workspace/copy";

import { useGraphNode } from "../api/queries";
import type { GraphNodeRelation, GraphNodeSource } from "../model/graph";
import { nodeTypeLabel } from "./nodeTypeLabel";

/** Relation items shown per group before a "Show all" expansion. */
export const RELATION_GROUP_PREVIEW = 10;

export interface NodeDetailPanelProps {
  projectId: string;
  graphVersion: number;
  nodeId: string;
  color: string;
  communityLabel: string;
  copy: WorkspaceCopy;
  locale: "en" | "zh";
  onClose: () => void;
  onAskAboutNode?: (label: string, sourceIds: string[]) => void;
  onOpenSource?: (sourceId: string, trigger: HTMLElement) => void;
  onSelectNode: (nodeId: string) => void;
}

/**
 * The relation group header is the mode (most frequent) `relationLabel`
 * among the group's edges, followed by the raw `relationType` code — the
 * mode is used (rather than, say, the first edge's label) because a single
 * `relationType` can carry minor label variants across extractions, and the
 * most common phrasing is the most representative heading.
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
  const libraryCopy = copy.library;
  const visibleEdges = expanded
    ? group.edges
    : group.edges.slice(0, RELATION_GROUP_PREVIEW);

  return (
    <div className="tap-graph-inspector-relation-group">
      <h4>
        {modeRelationLabel(group.edges)} ({group.relationType})
      </h4>
      <p>{libraryCopy.relationCount(group.edges.length)}</p>
      <ul className="tap-graph-inspector-relations">
        {visibleEdges.map((edge) => {
          const otherNodeId =
            edge.sourceNodeId === nodeId
              ? edge.targetNodeId
              : edge.sourceNodeId;
          const otherLabel = neighborLabelById.get(otherNodeId) ?? otherNodeId;
          return (
            <li key={edge.edgeId}>
              <button type="button" onClick={() => onSelectNode(otherNodeId)}>
                <span>{edge.relationLabel}</span>
                <strong>{otherLabel}</strong>
              </button>
            </li>
          );
        })}
      </ul>
      {!expanded && group.edges.length > RELATION_GROUP_PREVIEW ? (
        <button type="button" onClick={() => setExpanded(true)}>
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
}: {
  source: GraphNodeSource;
  copy: WorkspaceCopy;
  onOpenSource?: (sourceId: string, trigger: HTMLElement) => void;
}) {
  const libraryCopy = copy.library;
  const openOriginal = (event: { currentTarget: HTMLElement }) =>
    onOpenSource?.(source.sourceRevisionId, event.currentTarget);
  const evidence = source.evidence ?? [];
  return (
    <div className="tap-graph-inspector-source-group">
      <h5>{source.sourceName ?? source.sourceRevisionId}</h5>
      {evidence.length > 0 ? (
        <div>
          {evidence.map((item) => (
            <div key={item.chunkId}>
              {item.snippet !== null && item.snippet !== undefined ? (
                <p>{item.snippet}</p>
              ) : null}
              {onOpenSource !== undefined ? (
                <button type="button" onClick={openOriginal}>
                  {libraryCopy.openOriginal}
                </button>
              ) : null}
            </div>
          ))}
        </div>
      ) : onOpenSource !== undefined ? (
        <button type="button" onClick={openOriginal}>
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
  onSelectNode,
}: NodeDetailPanelProps) {
  const libraryCopy = copy.library;
  const nodeDetailQuery = useGraphNode(projectId, graphVersion, nodeId);
  const detail = nodeDetailQuery.data;

  if (nodeDetailQuery.isPending) {
    return <p role="status">{libraryCopy.nodeDetailsLoading}</p>;
  }
  if (nodeDetailQuery.isError || detail === undefined) {
    return <p role="alert">{libraryCopy.nodeDetailsError}</p>;
  }

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
      <div className="tap-graph-inspector-title">
        <span
          style={{ "--tap-community-color": color } as CSSProperties}
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
          <dd>{communityLabel}</dd>
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
      {sources.length > 0 ? (
        <div>
          <h4>{libraryCopy.evidenceSnippets}</h4>
          {sources.map((source) => (
            <SourceGroup
              key={source.sourceRevisionId}
              source={source}
              copy={copy}
              onOpenSource={onOpenSource}
            />
          ))}
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
    </>
  );
}
