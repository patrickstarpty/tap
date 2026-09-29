import { Segmented } from "antd";

import type { PrototypeCopy } from "./copy";
import type { LibrarySource } from "./model";

export type GraphView =
  | { kind: "domain" }
  | { kind: "source"; sourceId: string | null };

function firstPublishedGraphSourceId(
  sources: readonly LibrarySource[],
): string | null {
  const match = sources.find(
    (source) => source.status === "ready" && source.hasPublishedGraph,
  );
  return match?.id ?? null;
}

export function GraphViewSwitch({
  copy,
  sources,
  value,
  onChange,
}: {
  copy: PrototypeCopy;
  sources: readonly LibrarySource[];
  value: GraphView;
  onChange: (value: GraphView) => void;
}) {
  const readySources = sources.filter((source) => source.status === "ready");

  return (
    <div className="tap-library-graph-switch">
      <Segmented
        value={value.kind}
        onChange={(kind) => {
          if (kind === "domain") {
            onChange({ kind: "domain" });
            return;
          }
          onChange({
            kind: "source",
            sourceId: firstPublishedGraphSourceId(sources),
          });
        }}
        options={[
          { label: copy.library.graphDomain, value: "domain" },
          { label: copy.library.graphPublished, value: "source" },
        ]}
      />
      {value.kind === "source" ? (
        <label className="tap-library-graph-source-select">
          <span>{copy.library.graphSource}</span>
          <select
            aria-label={copy.library.graphSource}
            value={value.sourceId ?? ""}
            onChange={(event) =>
              onChange({
                kind: "source",
                sourceId: event.target.value || null,
              })
            }
          >
            <option value="" disabled>
              {copy.library.graphSource}
            </option>
            {readySources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
    </div>
  );
}
