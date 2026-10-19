import { Button } from "antd";
import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";

import {
  useAgents,
  useSetAgentEnabled,
  useSkills,
  type AiAgentRevision,
  type AiAgentRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import { useModelCatalog } from "../../../../features/knowledge/api/modelCatalog";
import {
  filterAssetWorkspaceItems,
  type AssetWorkspaceFilter,
} from "../assetWorkspaceFilter";
import type { WorkspaceCopy } from "../copy";
import { AgentDetailDrawer } from "./AgentDetailDrawer";
import { AgentForm } from "./AgentForm";

export interface AgentsWorkspaceProps {
  projectId: string;
  copy: WorkspaceCopy;
  onUse: (revisionId: string) => void;
  /** Opens this revision's detail drawer once, e.g. when a turn's "Sources
   * and settings" link names an Agent revision. */
  initialDetailRevisionId?: string | null;
  /** Called once `initialDetailRevisionId` has been applied, so the
   * caller can clear it — otherwise a later remount (this workspace is
   * conditionally rendered, so switching modules away and back unmounts
   * it) would see the same non-null id again and reopen the same
   * revision's drawer on every later visit. */
  onInitialDetailConsumed?: () => void;
}

type FormState =
  | { mode: "create" }
  | { mode: "edit"; agent: AiAgentRevisionDetail }
  | { mode: "copy"; agent: AiAgentRevisionDetail };

export function AgentsWorkspace({
  projectId,
  copy,
  onUse,
  initialDetailRevisionId = null,
  onInitialDetailConsumed,
}: AgentsWorkspaceProps) {
  const text = copy.agentsWorkspace;
  const isChinese = text.heading !== "Agents";
  const [filter, setFilter] = useState<AssetWorkspaceFilter>("all");
  const [detailRevisionId, setDetailRevisionId] = useState<string | null>(null);
  const [formState, setFormState] = useState<FormState | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const consumedInitialDetailRef = useRef<string | null>(null);

  useEffect(() => {
    if (
      initialDetailRevisionId !== null &&
      consumedInitialDetailRef.current !== initialDetailRevisionId
    ) {
      consumedInitialDetailRef.current = initialDetailRevisionId;
      setDetailRevisionId(initialDetailRevisionId);
      onInitialDetailConsumed?.();
    }
  }, [initialDetailRevisionId, onInitialDetailConsumed]);

  const agentsQuery = useAgents(projectId);
  const skillsQuery = useSkills(projectId);
  const modelCatalog = useModelCatalog(projectId);
  const setEnabled = useSetAgentEnabled(projectId);

  const enabledSkills = useMemo(
    () =>
      (skillsQuery.data ?? []).filter(
        (item) => item.isLatest && item.status === "enabled",
      ),
    [skillsQuery.data],
  );
  const chatModels = useMemo(
    () =>
      (modelCatalog.data?.items ?? []).filter(
        (model) =>
          model.capabilities.includes("chat") &&
          model.capabilities.includes("structured"),
      ),
    [modelCatalog.data],
  );

  const latestItems = useMemo(
    () => (agentsQuery.data ?? []).filter((item) => item.isLatest),
    [agentsQuery.data],
  );

  const visibleItems = useMemo(
    () => filterAssetWorkspaceItems(latestItems, filter),
    [filter, latestItems],
  );

  const openCreate = (event: MouseEvent<HTMLElement>) => {
    triggerRef.current = event.currentTarget;
    setFormState({ mode: "create" });
  };

  const openDetail = (
    item: AiAgentRevision,
    event: MouseEvent<HTMLElement>,
  ) => {
    triggerRef.current = event.currentTarget;
    setDetailRevisionId(item.revisionId);
  };

  return (
    <section
      className="tap-module tap-agents-workspace"
      aria-labelledby="agents-heading"
    >
      <header className="tap-module-heading">
        <div>
          <h1 id="agents-heading">{text.heading}</h1>
          <p>{text.description}</p>
        </div>
        <Button type="primary" onClick={openCreate}>
          {text.createAgent}
        </Button>
      </header>

      <div className="tap-catalog-toolbar">
        <label>
          <span>{isChinese ? "筛选" : "Filter"}</span>
          <select
            aria-label={isChinese ? "筛选" : "Filter"}
            value={
              filter === "all"
                ? text.filterAll
                : filter === "built-in"
                  ? text.filterBuiltIn
                  : filter === "custom"
                    ? text.filterCustom
                    : text.filterDisabled
            }
            onChange={(event) => {
              const { value } = event.target;
              if (value === text.filterBuiltIn) setFilter("built-in");
              else if (value === text.filterCustom) setFilter("custom");
              else if (value === text.filterDisabled) setFilter("disabled");
              else setFilter("all");
            }}
          >
            <option value={text.filterAll}>{text.filterAll}</option>
            <option value={text.filterBuiltIn}>{text.filterBuiltIn}</option>
            <option value={text.filterCustom}>{text.filterCustom}</option>
            <option value={text.filterDisabled}>{text.filterDisabled}</option>
          </select>
        </label>
        <span>{visibleItems.length}</span>
      </div>

      {agentsQuery.isPending ? (
        <p role="status">{text.loading}</p>
      ) : agentsQuery.isError ? (
        <div role="alert">
          <p>{text.loadFailed}</p>
          <Button onClick={() => void agentsQuery.refetch()}>
            {text.retry}
          </Button>
        </div>
      ) : visibleItems.length === 0 ? (
        <div className="tap-catalog-empty">{text.noResults}</div>
      ) : (
        <ul className="tap-catalog-list" aria-label={text.heading}>
          {visibleItems.map((item) => (
            <li key={item.revisionId} aria-label={item.displayName}>
              <article>
                <div className="tap-catalog-title">
                  <h2>
                    <button
                      type="button"
                      onClick={(event) => openDetail(item, event)}
                    >
                      {item.displayName}
                    </button>
                  </h2>
                  <span data-source-kind={item.sourceKind}>
                    {item.sourceKind === "built_in"
                      ? text.filterBuiltIn
                      : text.filterCustom}
                  </span>
                  <span data-status={item.status}>
                    {item.status === "enabled"
                      ? text.statusEnabled
                      : text.statusDisabled}
                  </span>
                </div>
                {item.description ? <p>{item.description}</p> : null}
                {item.hasSkillUpdates ? <p>{text.hasSkillUpdates}</p> : null}
              </article>
              <div className="tap-catalog-actions">
                {item.status === "enabled" ? (
                  <Button
                    type="primary"
                    ghost
                    onClick={() => onUse(item.revisionId)}
                  >
                    {text.useInChat}
                  </Button>
                ) : null}
                {/* Built-in Agents are immutable server-side (revise/disable
                    both reject them) — the UI mirrors that by never
                    offering Disable here; "Copy as custom" (from the
                    detail drawer) is the only way to build on one. */}
                {item.sourceKind === "built_in" ? null : (
                  <Button
                    loading={setEnabled.isPending}
                    onClick={() =>
                      setEnabled.mutate({
                        agentId: item.agentId,
                        enabled: item.status !== "enabled",
                      })
                    }
                  >
                    {item.status === "enabled" ? text.disable : text.enable}
                  </Button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {detailRevisionId !== null ? (
        <AgentDetailDrawer
          projectId={projectId}
          copy={copy}
          revisionId={detailRevisionId}
          skills={enabledSkills}
          onClose={() => setDetailRevisionId(null)}
          onUse={onUse}
          onEdit={(detail) => {
            setDetailRevisionId(null);
            setFormState({ mode: "edit", agent: detail });
          }}
          onCopyAsCustom={(detail) => {
            setDetailRevisionId(null);
            setFormState({ mode: "copy", agent: detail });
          }}
          onUpgraded={(newRevisionId) => setDetailRevisionId(newRevisionId)}
          opener={triggerRef.current}
        />
      ) : null}

      {formState !== null ? (
        <AgentForm
          projectId={projectId}
          copy={copy}
          mode={formState.mode}
          agent={formState.mode === "create" ? undefined : formState.agent}
          skills={enabledSkills}
          models={chatModels}
          onClose={() => setFormState(null)}
          opener={triggerRef.current}
        />
      ) : null}
    </section>
  );
}
