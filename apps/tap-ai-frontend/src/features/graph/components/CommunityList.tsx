import type { CSSProperties, ReactNode } from "react";
// `import type` only: dependency-cruiser's `features-do-not-import-upward`
// rule (features/ may not import widgets/) does not flag type-only imports
// (verified against `dependency-cruiser.cjs`), only value imports. This
// module never imports a widgets *value*.
import type { WorkspaceCopy } from "../../../widgets/tap/workspace/copy";

export type CommunityListCopy = WorkspaceCopy["library"];

export interface CommunitySummary {
  communityId: string;
  label: string;
  color: string;
  size: number;
}

export function CommunityList({
  communities,
  selected,
  onToggle,
  onSelectAll,
  footer,
  notice = null,
  copy,
}: {
  communities: readonly CommunitySummary[];
  selected: ReadonlySet<string>;
  onToggle: (communityId: string) => void;
  onSelectAll: (all: boolean) => void;
  footer: { extracting: number; partial: number };
  // Status content that belongs with the community column (e.g. a "the
  // graph is being rebuilt" notice) rather than floating as an unplaced
  // child of the canvas layout's grid.
  notice?: ReactNode;
  copy: CommunityListCopy;
}) {
  const allSelected = communities.every((community) =>
    selected.has(community.communityId),
  );
  const someSelected = communities.some((community) =>
    selected.has(community.communityId),
  );

  return (
    <aside className="tap-graph-communities" aria-label={copy.communities}>
      <h2>{copy.communities}</h2>
      {notice}
      <label className="tap-graph-select-all">
        <input
          type="checkbox"
          checked={allSelected}
          ref={(input) => {
            if (input) input.indeterminate = someSelected && !allSelected;
          }}
          onChange={(event) => onSelectAll(event.target.checked)}
        />
        <span>{copy.selectAllTopics}</span>
      </label>
      <div className="tap-graph-community-list">
        {communities.map((community) => (
          <label
            key={community.communityId}
            style={
              { "--tap-community-color": community.color } as CSSProperties
            }
          >
            <input
              type="checkbox"
              checked={selected.has(community.communityId)}
              aria-label={`${community.label} · ${community.size} ${copy.nodes}`}
              onChange={() => onToggle(community.communityId)}
            />
            <span
              className="tap-graph-community-dot"
              style={
                { "--tap-community-color": community.color } as CSSProperties
              }
              aria-hidden="true"
            />
            <span className="tap-graph-community-name" title={community.label}>
              {community.label}
            </span>
            <small>{community.size}</small>
          </label>
        ))}
      </div>
      {footer.extracting > 0 || footer.partial > 0 ? (
        <p className="tap-graph-extraction-footer" role="status">
          {footer.extracting > 0 ? (
            <span>{copy.extractingSources(footer.extracting)}</span>
          ) : null}
          {footer.partial > 0 ? (
            <span>{copy.partialSources(footer.partial)}</span>
          ) : null}
        </p>
      ) : null}
      <p>{copy.graphNavigationHint}</p>
    </aside>
  );
}
