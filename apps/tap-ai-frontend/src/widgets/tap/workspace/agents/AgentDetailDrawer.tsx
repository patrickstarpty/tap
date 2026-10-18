import { Button } from "antd";
import { useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";

import {
  useAgent,
  useAgentRevisions,
  type AiAgentRevision,
  type SkillRevision,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import { AccessibleDialog } from "../AccessibleDialog";
import type { WorkspaceCopy } from "../copy";

type DetailTab = "overview" | "skills" | "revisions";

export interface AgentDetailDrawerProps {
  projectId: string;
  copy: WorkspaceCopy;
  revisionId: string;
  skills: readonly SkillRevision[];
  onClose: () => void;
  onUse: (revisionId: string) => void;
  onEdit: (detail: AiAgentRevision & { systemInstruction?: string }) => void;
  opener: HTMLElement | null;
}

async function fetchSkillRevisionDetail(
  projectId: string,
  revisionId: string,
): Promise<SkillRevisionDetail> {
  const response = await fetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/ai/skills/${encodeURIComponent(revisionId)}`,
  );
  if (!response.ok) throw new Error("Skill revision is unavailable.");
  return (await response.json()) as SkillRevisionDetail;
}

export function AgentDetailDrawer({
  projectId,
  copy,
  revisionId,
  skills,
  onClose,
  onUse,
  onEdit,
  opener,
}: AgentDetailDrawerProps) {
  const text = copy.agentsWorkspace;
  const [tab, setTab] = useState<DetailTab>("overview");
  const detailQuery = useAgent(projectId, revisionId);
  const detail = detailQuery.data;
  const revisionsQuery = useAgentRevisions(projectId, detail?.agentId ?? null);

  const attachedSkillIds = detail?.skillRevisionIds ?? [];
  const attachedSkillQueries = useQueries({
    queries: attachedSkillIds.map((id) => ({
      queryKey: ["ai-assets", projectId, "skill", id],
      queryFn: () => fetchSkillRevisionDetail(projectId, id),
      staleTime: 30_000,
    })),
  });
  const attachedSkillNames = useMemo(
    () =>
      attachedSkillQueries
        .map((query) => query.data?.displayName)
        .filter((name): name is string => name !== undefined),
    [attachedSkillQueries],
  );

  const latestBySkillId = useMemo(() => {
    const map = new Map<string, string>();
    for (const skill of skills) map.set(skill.skillId, skill.revisionId);
    return map;
  }, [skills]);

  // Getting an agent by revision only ever returns the Summary shape (no
  // `systemInstruction`, which the backend never exposes again after
  // create/revise), so an upgrade can't be auto-submitted here — it opens
  // the edit form, pre-selecting each bundled skill's latest revision, and
  // lets the person confirm the (required) system instruction before
  // saving the new revision.
  const upgrade = () => {
    if (detail === undefined) return;
    const upgradedSkillIds = attachedSkillQueries
      .map((query) => query.data)
      .filter((value): value is SkillRevisionDetail => value !== undefined)
      .map(
        (skillDetail) =>
          latestBySkillId.get(skillDetail.skillId) ?? skillDetail.revisionId,
      );
    onEdit({ ...detail, skillRevisionIds: upgradedSkillIds });
  };

  const tabs: { id: DetailTab; label: string }[] = [
    { id: "overview", label: text.tabs.overview },
    { id: "skills", label: text.tabs.skills },
    { id: "revisions", label: text.tabs.revisions },
  ];

  return (
    <AccessibleDialog
      ariaLabel={detail?.displayName ?? text.heading}
      className="tap-agent-detail-drawer"
      onClose={onClose}
      opener={opener}
    >
      <header>
        <h2>{detail?.displayName ?? text.heading}</h2>
        <Button onClick={onClose}>{text.close}</Button>
      </header>
      {detailQuery.isPending ? null : detail === undefined ? null : (
        <>
          {detail.hasSkillUpdates ? (
            <div role="status" className="tap-agent-skill-updates">
              <p>{text.hasSkillUpdates}</p>
              <Button onClick={upgrade}>{text.upgrade}</Button>
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
              <ul>
                {attachedSkillNames.map((name) => (
                  <li key={name}>{name}</li>
                ))}
              </ul>
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
            {detail.builtIn ? null : (
              <Button onClick={() => onEdit(detail)}>{text.edit}</Button>
            )}
          </div>
        </>
      )}
    </AccessibleDialog>
  );
}
