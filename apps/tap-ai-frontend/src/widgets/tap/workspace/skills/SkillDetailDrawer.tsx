import { Button } from "antd";
import { useEffect, useState } from "react";

import {
  skillFileUrl,
  useSkill,
  useSkillRevisions,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import { AccessibleDialog } from "../AccessibleDialog";
import type { WorkspaceCopy } from "../copy";

// Files in these categories never enter a prompt (spec §1.3) -- they are
// archived verbatim and only ever downloaded, never executed.
const NOT_EXECUTED_CATEGORIES = new Set(["script", "asset", "other"]);

type DetailTab = "instructions" | "files" | "metadata" | "revisions";

export interface SkillDetailDrawerProps {
  projectId: string;
  copy: WorkspaceCopy;
  revisionId: string;
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
  onClose,
  onUse,
  onEdit,
  onCopyAsCustom,
  opener,
}: SkillDetailDrawerProps) {
  const text = copy.skillsWorkspace;
  const [tab, setTab] = useState<DetailTab>("instructions");
  // I-7: a revision switcher on the Revision history tab -- clicking an
  // older/newer revision re-points the drawer at it in place, rather than
  // only ever showing the revision the caller opened it with.
  const [activeRevisionId, setActiveRevisionId] = useState(revisionId);
  useEffect(() => {
    setActiveRevisionId(revisionId);
  }, [revisionId]);
  const detailQuery = useSkill(projectId, activeRevisionId);
  const detail = detailQuery.data;
  const revisionsQuery = useSkillRevisions(projectId, detail?.skillId ?? null);

  const tabs: { id: DetailTab; label: string }[] = [
    { id: "instructions", label: text.tabs.instructions },
    { id: "files", label: text.tabs.files },
    { id: "metadata", label: text.tabs.metadata },
    { id: "revisions", label: text.tabs.revisions },
  ];

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
            {tabs.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                onClick={() => setTab(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
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
          {tab === "metadata" ? (
            <div role="tabpanel">
              <dl>
                <dt>{text.applicableTasks}</dt>
                <dd>
                  {detail.applicableTasks
                    .map((task) => text.taskLabels[task])
                    .join(", ")}
                </dd>
                <dt>{text.revision}</dt>
                <dd>{detail.revisionId}</dd>
              </dl>
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
