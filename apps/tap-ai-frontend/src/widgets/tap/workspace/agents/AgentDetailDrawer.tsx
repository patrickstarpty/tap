import { Button } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";

import {
  aiAssetKeys,
  AiAssetError,
  fetchSkillRevisionDetail,
  useAgent,
  useAgentRevisions,
  useReviseAgent,
  type AiAgentRevisionDetail,
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
  onEdit: (detail: AiAgentRevisionDetail) => void;
  onCopyAsCustom: (detail: AiAgentRevisionDetail) => void;
  /** Called after Upgrade successfully creates a new revision, with that
   * revision's id — the caller should move the drawer onto it (otherwise
   * it keeps showing the now-superseded revision it upgraded from). */
  onUpgraded?: (revisionId: string) => void;
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
  onUpgraded,
  opener,
}: AgentDetailDrawerProps) {
  const text = copy.agentsWorkspace;
  const [tab, setTab] = useState<DetailTab>("overview");
  const [upgradeError, setUpgradeError] = useState<string | null>(null);
  const detailQuery = useAgent(projectId, revisionId);
  const detail = detailQuery.data;
  const revisionsQuery = useAgentRevisions(projectId, detail?.agentId ?? null);
  const reviseAgent = useReviseAgent(projectId);

  useEffect(() => {
    setUpgradeError(null);
  }, [revisionId]);

  const attachedSkillIds = detail?.skillRevisionIds ?? [];
  const attachedSkillQueries = useQueries({
    queries: attachedSkillIds.map((id) => ({
      queryKey: aiAssetKeys.skill(projectId, id),
      queryFn: () => fetchSkillRevisionDetail(projectId, id),
      staleTime: 30_000,
    })),
  });
  const attachedSkillsPending = attachedSkillQueries.some(
    (query) => query.isPending,
  );
  const attachedSkillsErrored = attachedSkillQueries.some(
    (query) => query.isError,
  );
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

  // Each bundled Skill's latest revision can only be resolved once its own
  // (currently-attached) revision's `skillId` is known, so Upgrade stays
  // disabled until every lookup settles — upgrading from a partial set
  // would silently drop whichever skill hadn't resolved yet.
  // `systemInstruction` is optional on the wire (a revision whose stored
  // value is absent still returns 200 — see the backend fix for the
  // former 500) but `AgentReviseRequest` requires a non-empty one, so an
  // Agent missing it can't be auto-upgraded (nothing to resubmit).
  const systemInstructionMissing =
    detail?.systemInstruction == null || detail.systemInstruction.length === 0;

  const upgrade = () => {
    if (
      detail === undefined ||
      detail.builtIn ||
      attachedSkillsPending ||
      attachedSkillsErrored ||
      systemInstructionMissing
    )
      return;
    setUpgradeError(null);
    const upgradedSkillIds = attachedSkillQueries
      .map((query) => query.data)
      .filter((value): value is SkillRevisionDetail => value !== undefined)
      .map(
        (skillDetail) =>
          latestBySkillId.get(skillDetail.skillId) ?? skillDetail.revisionId,
      );
    reviseAgent.mutate(
      {
        agentId: detail.agentId,
        request: {
          baseRevisionId: detail.revisionId,
          name: detail.displayName,
          description: detail.description ?? "",
          systemInstruction: detail.systemInstruction ?? "",
          defaultModelAlias: detail.defaultModelAlias ?? null,
          skillRevisionIds: upgradedSkillIds,
        },
      },
      {
        onSuccess: (result) => onUpgraded?.(result.revisionId),
        onError: (cause) => {
          setUpgradeError(
            cause instanceof AiAssetError && cause.code === "revision_conflict"
              ? text.revisionConflict
              : text.upgradeFailed,
          );
        },
      },
    );
  };

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
          {detail.hasSkillUpdates ? (
            <div role="status" className="tap-agent-skill-updates">
              <p>{text.hasSkillUpdates}</p>
              {detail.builtIn ? null : (
                // The server rejects revising a built-in Agent outright
                // (agent_built_in_immutable), so Upgrade is never offered
                // for one — "Copy as custom" (below, in the dialog
                // actions) is the only way to build on it.
                <Button
                  loading={reviseAgent.isPending}
                  disabled={
                    attachedSkillsPending ||
                    attachedSkillsErrored ||
                    systemInstructionMissing
                  }
                  onClick={upgrade}
                >
                  {text.upgrade}
                </Button>
              )}
              {upgradeError !== null ? (
                <p role="alert">{upgradeError}</p>
              ) : null}
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
              {attachedSkillsErrored ? (
                <p role="alert">{text.loadFailed}</p>
              ) : (
                <ul>
                  {attachedSkillNames.map((name) => (
                    <li key={name}>{name}</li>
                  ))}
                </ul>
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
