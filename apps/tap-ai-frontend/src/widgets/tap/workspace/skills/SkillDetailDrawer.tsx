import { Button } from "antd";
import { useEffect, useState } from "react";

import type { TeamPeriod } from "../../../../features/knowledge/api/agentTeam";
import {
  skillFileUrl,
  useSkill,
  useSkillRevisions,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import {
  useSkillHubDetail,
  type SkillHubDetailView,
} from "../../../../features/knowledge/api/skillHub";
import { AccessibleDialog } from "../AccessibleDialog";
import { formatDateTime, teamLocale } from "../agents/team/format";
import type { WorkspaceCopy } from "../copy";
import { RevisionCompare } from "./RevisionCompare";
import { SkillSettingsPanel } from "./SkillSettingsPanel";
import { SkillUsagePanel } from "./SkillUsagePanel";
import "./skillHub.css";

// Files in these categories never enter a prompt (spec §1.3) -- they are
// archived verbatim and only ever downloaded, never executed.
const NOT_EXECUTED_CATEGORIES = new Set(["script", "asset", "other"]);

type DetailTab =
  "overview" | "instructions" | "files" | "revisions" | "usage" | "settings";

const DETAIL_TABS: DetailTab[] = [
  "overview",
  "instructions",
  "files",
  "revisions",
  "usage",
  "settings",
];

function OverviewPanel({
  view,
  revisionId,
  copy,
}: {
  view: SkillHubDetailView;
  revisionId: string;
  copy: WorkspaceCopy;
}) {
  const hub = copy.skillHub;
  const text = hub.detail;
  const skill = view.data.skill;
  const locale = teamLocale(copy);
  const time = (value: string | null) =>
    value === null
      ? text.notSpecified
      : formatDateTime(value, view.timezone, locale);
  const taskLabels: Record<string, string> = copy.skillsWorkspace.taskLabels;
  const imported = skill.sourceKind === "imported";
  const rows: [string, string][] = [
    [text.fields.category, hub.categories[skill.category]],
    [text.fields.version, skill.version],
    [text.fields.source, hub.sources[skill.sourceKind]],
    // "Imported as" only describes imported Skills; authored and built-in
    // ones already say how they came to be in the Source row.
    ...(imported
      ? ([
          [text.fields.originKind, text.originKinds[skill.originKind]],
          [text.fields.originRef, skill.originRef ?? text.notSpecified],
          [text.fields.importedAt, time(skill.originImportedAt)],
        ] as [string, string][])
      : []),
    [
      imported ? text.fields.importedBy : text.fields.createdBy,
      skill.originActorId,
    ],
    [text.fields.owner, skill.ownerActorId],
    [text.fields.ownerSince, time(skill.ownerAssignedAt)],
    [text.fields.compatibility, skill.compatibility ?? text.notSpecified],
    [
      text.fields.applicableTasks,
      skill.applicableTasks.map((task) => taskLabels[task] ?? task).join(", "),
    ],
    [text.fields.revision, revisionId],
  ];
  return (
    <>
      <h3>{text.fields.description}</h3>
      <p>{skill.description ?? text.noDescription}</p>
      <dl className="tap-skill-overview">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

export interface SkillDetailDrawerProps {
  projectId: string;
  copy: WorkspaceCopy;
  revisionId: string;
  /** The library's resolved period, for the usage figures. */
  period: TeamPeriod;
  onClose: () => void;
  onUse: (revisionId: string) => void;
  onEdit: (detail: SkillRevisionDetail) => void;
  onCopyAsCustom: (detail: SkillRevisionDetail) => void;
  opener: HTMLElement | null;
}

export function SkillDetailDrawer({
  projectId,
  copy,
  revisionId,
  period,
  onClose,
  onUse,
  onEdit,
  onCopyAsCustom,
  opener,
}: SkillDetailDrawerProps) {
  const text = copy.skillsWorkspace;
  const [tab, setTab] = useState<DetailTab>("overview");
  // I-7: a revision switcher on the Revision history tab -- clicking an
  // older/newer revision re-points the drawer at it in place, rather than
  // only ever showing the revision the caller opened it with.
  const [activeRevisionId, setActiveRevisionId] = useState(revisionId);
  useEffect(() => {
    setActiveRevisionId(revisionId);
  }, [revisionId]);
  const detailQuery = useSkill(projectId, activeRevisionId);
  const detail = detailQuery.data;
  const skillId = detail?.skillId ?? null;
  const revisionsQuery = useSkillRevisions(projectId, skillId);
  const hubQuery = useSkillHubDetail(projectId, skillId, period);
  const hubView = hubQuery.data;
  const tabLabels = copy.skillHub.detail.tabs;

  // The overview and settings tabs read the Skill's library record and
  // share one loading/failure state for it.
  const hubFallback =
    hubView === undefined ? (
      hubQuery.isError ? (
        <div role="alert">
          <p>{copy.skillHub.detail.loadFailed}</p>
          <Button onClick={() => void hubQuery.refetch()}>{text.retry}</Button>
        </div>
      ) : (
        <p role="status">{text.loading}</p>
      )
    ) : null;

  return (
    <AccessibleDialog
      ariaLabel={detail?.displayName ?? text.heading}
      className="tap-skill-detail-drawer tap-catalog-dialog"
      onClose={onClose}
      opener={opener}
    >
      <header>
        <h2>{detail?.displayName ?? text.heading}</h2>
        <Button onClick={onClose}>{text.close}</Button>
      </header>
      {detailQuery.isPending ? (
        <p role="status">{text.loading}</p>
      ) : detailQuery.isError || detail === undefined ? (
        // I-7: an error no longer leaves a blank drawer body.
        <div role="alert">
          <p>{text.loadFailed}</p>
          <Button onClick={() => void detailQuery.refetch()}>
            {text.retry}
          </Button>
        </div>
      ) : (
        <>
          <div role="tablist">
            {DETAIL_TABS.map((id) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
              >
                {tabLabels[id]}
              </button>
            ))}
          </div>
          {tab === "overview" ? (
            <div role="tabpanel">
              {hubView === undefined ? (
                hubFallback
              ) : (
                <OverviewPanel
                  view={hubView}
                  revisionId={detail.revisionId}
                  copy={copy}
                />
              )}
            </div>
          ) : null}
          {tab === "instructions" ? (
            <div role="tabpanel">
              <pre>{detail.instructions ?? ""}</pre>
              <h3>{text.attachedReferences}</h3>
              {detail.attachedReferences !== null &&
              detail.attachedReferences !== undefined &&
              detail.attachedReferences.length > 0 ? (
                <pre>{detail.attachedReferences}</pre>
              ) : (
                <p>{text.noAttachedReferences}</p>
              )}
            </div>
          ) : null}
          {tab === "files" ? (
            <div role="tabpanel">
              <ul>
                {(detail.files ?? []).map((file) => {
                  const categoryLabel =
                    file.category === "reference"
                      ? file.attached
                        ? text.import.category.referenceAttached
                        : text.import.category.referenceUnattached
                      : file.category === "skill"
                        ? text.import.category.skill
                        : file.category === "script"
                          ? text.import.category.script
                          : file.category === "asset"
                            ? text.import.category.asset
                            : file.category;
                  const notExecuted =
                    NOT_EXECUTED_CATEGORIES.has(file.category) ||
                    (file.category === "reference" && !file.attached);
                  return (
                    <li key={file.relativePath}>
                      <span>{file.relativePath}</span>
                      <span className="tap-skill-file-category">
                        {categoryLabel}
                      </span>
                      {notExecuted ? (
                        <span className="tap-skill-file-not-executed">
                          {text.savedNotExecuted}
                        </span>
                      ) : null}
                      <a
                        href={skillFileUrl(
                          projectId,
                          detail.revisionId,
                          file.relativePath,
                        )}
                        download
                      >
                        {text.download}
                      </a>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
          {tab === "revisions" ? (
            <div role="tabpanel">
              <ul>
                {(revisionsQuery.data ?? []).map((revision) => (
                  <li key={revision.revisionId}>
                    {revision.revisionId === activeRevisionId ? (
                      <strong>{revision.revisionId}</strong>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setActiveRevisionId(revision.revisionId)}
                      >
                        {revision.revisionId}
                      </button>
                    )}
                  </li>
                ))}
              </ul>
              <RevisionCompare
                projectId={projectId}
                skillId={detail.skillId}
                copy={copy}
              />
            </div>
          ) : null}
          {tab === "usage" ? (
            <div role="tabpanel">
              <SkillUsagePanel
                projectId={projectId}
                skillId={detail.skillId}
                period={period}
                copy={copy}
              />
            </div>
          ) : null}
          {tab === "settings" ? (
            <div role="tabpanel">
              {hubView === undefined ? (
                hubFallback
              ) : (
                <SkillSettingsPanel
                  projectId={projectId}
                  detail={hubView.data}
                  copy={copy}
                  // Same rule as Edit below: a new revision builds on the
                  // latest revision of a Skill created in Tapper.
                  onEdit={
                    detail.isLatest && detail.sourceKind === "authored"
                      ? () => onEdit(detail)
                      : undefined
                  }
                />
              )}
            </div>
          ) : null}
          <div className="tap-dialog-actions">
            {/* The revision switcher (above) can point this drawer at an
             * older revision; "Use in chat" and Edit must act on that
             * revision specifically, not silently on the latest one, so
             * both are only offered while viewing the latest revision --
             * otherwise Edit would submit a stale baseRevisionId (409
             * revision_conflict) and "Use in chat" would apply the wrong
             * instructions. */}
            {detail.status === "enabled" && detail.isLatest ? (
              <Button
                type="primary"
                ghost
                onClick={() => onUse(detail.revisionId)}
              >
                {text.useInChat}
              </Button>
            ) : null}
            {detail.builtIn ? (
              <Button onClick={() => onCopyAsCustom(detail)}>
                {text.copyAsCustom}
              </Button>
            ) : detail.sourceKind === "imported" ? (
              // I-3: editing an imported Skill in place would rebuild a
              // SKILL.md-only package and silently drop the original
              // package's references/scripts/assets/unknown frontmatter --
              // point at re-importing instead of offering Edit.
              <p className="tap-skill-edit-unavailable">
                {text.editUnavailableImported}
              </p>
            ) : detail.isLatest ? (
              <Button onClick={() => onEdit(detail)}>{text.edit}</Button>
            ) : null}
          </div>
        </>
      )}
    </AccessibleDialog>
  );
}
