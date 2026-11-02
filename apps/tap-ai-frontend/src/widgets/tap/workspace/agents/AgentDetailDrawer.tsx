import { Button } from "antd";
import { useMemo, useState } from "react";

import {
  useAgent,
  useAgentRevisions,
  type AiAgentRevisionDetail,
  type SkillRevision,
} from "../../../../features/knowledge/api/aiAssets";
import { AccessibleDialog } from "../AccessibleDialog";
import type { WorkspaceCopy } from "../copy";

type DetailTab = "overview" | "skills" | "revisions";

export interface AgentDetailDrawerProps {
  projectId: string;
  copy: WorkspaceCopy;
  revisionId: string;
  /** The latest revision of every Skill in the library, any status, so a
   * pinned or excluded Skill that was disabled since can still be named. */
  skills: readonly SkillRevision[];
  onClose: () => void;
  onUse: (revisionId: string) => void;
  onEdit: (detail: AiAgentRevisionDetail) => void;
  onCopyAsCustom: (detail: AiAgentRevisionDetail) => void;
  opener: HTMLElement | null;
}

export function AgentDetailDrawer({
  projectId,
  copy,
  revisionId,
  skills,
  onClose,
  onUse,
  onEdit,
  onCopyAsCustom,
  opener,
}: AgentDetailDrawerProps) {
  const text = copy.agentsWorkspace;
  const [tab, setTab] = useState<DetailTab>("overview");
  const detailQuery = useAgent(projectId, revisionId);
  const detail = detailQuery.data;
  const revisionsQuery = useAgentRevisions(projectId, detail?.agentId ?? null);

  const skillById = useMemo(
    () => new Map(skills.map((skill) => [skill.skillId, skill])),
    [skills],
  );
  // Pinned and excluded Skills are kept by Skill, so there is no revision
  // to fall behind on -- a pinned Skill always loads its latest enabled
  // revision. Only a pinned Skill disabled since is worth a warning (I-6):
  // it is skipped when this Agent answers.
  const describeSkill = (skillId: string) => {
    const skill = skillById.get(skillId);
    return {
      skillId,
      name: skill?.displayName ?? skillId,
      disabled: skill === undefined || skill.status !== "enabled",
    };
  };
  const pinnedSkills = (detail?.pinnedSkillIds ?? []).map(describeSkill);
  const excludedSkills = (detail?.excludedSkillIds ?? []).map(describeSkill);
  const disabledPinnedSkillNames = pinnedSkills
    .filter((item) => item.disabled)
    .map((item) => item.name);

  const skillList = (
    label: string,
    empty: string,
    items: ReturnType<typeof describeSkill>[],
  ) => (
    <section>
      <h3>{label}</h3>
      {items.length === 0 ? (
        <p>{empty}</p>
      ) : (
        <ul aria-label={label}>
          {items.map((item) => (
            <li key={item.skillId}>
              {item.name}
              {item.disabled ? ` (${text.statusDisabled})` : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );

  const tabs: { id: DetailTab; label: string }[] = [
    { id: "overview", label: text.tabs.overview },
    { id: "skills", label: text.tabs.skills },
    { id: "revisions", label: text.tabs.revisions },
  ];

  return (
    <AccessibleDialog
      ariaLabel={detail?.displayName ?? text.heading}
      className="tap-agent-detail-drawer tap-catalog-dialog"
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
        <div role="alert">
          <p>{text.loadFailed}</p>
          <Button onClick={() => void detailQuery.refetch()}>
            {text.retry}
          </Button>
        </div>
      ) : (
        <>
          {disabledPinnedSkillNames.length > 0 ? (
            <div role="alert" className="tap-agent-skill-disabled-warning">
              <p>{text.pinnedSkillDisabledWarning}</p>
              <p>{disabledPinnedSkillNames.join(", ")}</p>
            </div>
          ) : null}
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
          {tab === "overview" ? (
            <div role="tabpanel">
              <p>{detail.description ?? ""}</p>
              <dl>
                <dt>{text.defaultModel}</dt>
                <dd>{detail.defaultModelAlias ?? text.defaultModelGlobal}</dd>
                <dt>{text.toolsLabel}</dt>
                <dd>{text.toolsBuiltIn}</dd>
                <dt>{text.revision}</dt>
                <dd>{detail.revisionId}</dd>
              </dl>
            </div>
          ) : null}
          {tab === "skills" ? (
            <div role="tabpanel">
              {skillList(text.pinnedSkills, text.noPinnedSkills, pinnedSkills)}
              {skillList(
                text.excludedSkills,
                text.noExcludedSkills,
                excludedSkills,
              )}
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
