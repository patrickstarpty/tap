import { useState } from "react";

import {
  useSkill,
  useSkillRevisions,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import type { WorkspaceCopy } from "../copy";
import { buildSkillMarkdown } from "./skillMarkdown";

export interface RevisionCompareProps {
  projectId: string;
  skillId: string;
  copy: WorkspaceCopy;
}

type Side = "left" | "right";

function frontmatterName(detail: SkillRevisionDetail): string {
  const name = detail.frontmatter?.name;
  return typeof name === "string" ? name : detail.displayName;
}

function RevisionPane({
  projectId,
  revisionId,
  label,
  copy,
}: {
  projectId: string;
  revisionId: string;
  label: string;
  copy: WorkspaceCopy;
}) {
  const query = useSkill(projectId, revisionId);
  const detail = query.data;
  return (
    <section aria-label={label} className="tap-skill-compare-pane">
      <h4>{label}</h4>
      {detail === undefined ? (
        query.isError ? (
          <p role="alert">{copy.skillsWorkspace.loadFailed}</p>
        ) : (
          <p role="status">{copy.skillHub.compare.loading}</p>
        )
      ) : (
        <pre>
          {buildSkillMarkdown({
            name: frontmatterName(detail),
            description: detail.description ?? "",
            instructions: detail.instructions ?? "",
          })}
        </pre>
      )}
    </section>
  );
}

/** Two revision pickers with both SKILL.md bodies rendered side by side. */
export function RevisionCompare({
  projectId,
  skillId,
  copy,
}: RevisionCompareProps) {
  const text = copy.skillHub.compare;
  const revisionsQuery = useSkillRevisions(projectId, skillId);
  const revisions = revisionsQuery.data ?? [];
  const [selected, setSelected] = useState<Record<Side, string | null>>({
    left: null,
    right: null,
  });

  const optionLabel = (revisionId: string) => {
    const revision = revisions.find((item) => item.revisionId === revisionId);
    if (revision === undefined) return revisionId;
    const version = revision.versionLabel ?? revision.revisionId;
    return revision.isLatest ? `${version} · ${text.latest}` : version;
  };
  const versionOf = (revisionId: string) => {
    const revision = revisions.find((item) => item.revisionId === revisionId);
    return revision?.versionLabel ?? revisionId;
  };
  const sides: { side: Side; label: string }[] = [
    { side: "left", label: text.left },
    { side: "right", label: text.right },
  ];

  return (
    <section
      className="tap-skill-compare"
      aria-labelledby="skill-compare-heading"
    >
      <h3 id="skill-compare-heading">{text.heading}</h3>
      {revisionsQuery.isError ? (
        <p role="alert">{copy.skillsWorkspace.loadFailed}</p>
      ) : null}
      <div className="tap-skill-compare-pickers">
        {sides.map(({ side, label }) => (
          <label key={side}>
            <span>{label}</span>
            <select
              aria-label={label}
              value={selected[side] ?? ""}
              onChange={(event) =>
                setSelected((prev) => ({
                  ...prev,
                  [side]: event.target.value === "" ? null : event.target.value,
                }))
              }
            >
              <option value="">{text.placeholder}</option>
              {revisions.map((revision) => (
                <option key={revision.revisionId} value={revision.revisionId}>
                  {optionLabel(revision.revisionId)}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {selected.left === null || selected.right === null ? (
        <p>{text.choose}</p>
      ) : null}
      <div className="tap-skill-compare-panes">
        {sides.map(({ side, label }) => {
          const revisionId = selected[side];
          if (revisionId === null) return null;
          return (
            <RevisionPane
              key={side}
              projectId={projectId}
              revisionId={revisionId}
              label={text.regionLabel
                .replace("{side}", label)
                .replace("{revision}", versionOf(revisionId))}
              copy={copy}
            />
          );
        })}
      </div>
    </section>
  );
}
