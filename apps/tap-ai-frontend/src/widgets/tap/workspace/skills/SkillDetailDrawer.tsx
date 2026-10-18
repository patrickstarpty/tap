import { Button } from "antd";
import { useState } from "react";

import {
  useSkill,
  useSkillRevisions,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import { AccessibleDialog } from "../AccessibleDialog";
import type { WorkspaceCopy } from "../copy";

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
  const detailQuery = useSkill(projectId, revisionId);
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
      className="tap-skill-detail-drawer"
      onClose={onClose}
      opener={opener}
    >
      <header>
        <h2>{detail?.displayName ?? text.heading}</h2>
        <Button onClick={onClose}>{text.close}</Button>
      </header>
      {detailQuery.isPending ? null : detail === undefined ? null : (
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
            </div>
          ) : null}
          {tab === "files" ? (
            <div role="tabpanel">
              <ul>
                {(detail.files ?? []).map((file) => (
                  <li key={file.relativePath}>{file.relativePath}</li>
                ))}
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
                  <li key={revision.revisionId}>{revision.revisionId}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="tap-dialog-actions">
            {detail.status === "enabled" ? (
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
            ) : (
              <Button onClick={() => onEdit(detail)}>{text.edit}</Button>
            )}
          </div>
        </>
      )}
    </AccessibleDialog>
  );
}
