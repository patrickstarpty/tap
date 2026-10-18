import { Button } from "antd";
import { useMemo, useRef, useState, type MouseEvent } from "react";

import {
  useAgents,
  useSetAgentEnabled,
  useSkills,
  type AiAgentRevision,
} from "../../../../features/knowledge/api/aiAssets";
import { useModelCatalog } from "../../../../features/knowledge/api/modelCatalog";
import type { WorkspaceCopy } from "../copy";
import { AgentDetailDrawer } from "./AgentDetailDrawer";
import { AgentForm } from "./AgentForm";

export interface AgentsWorkspaceProps {
  projectId: string;
  copy: WorkspaceCopy;
  onUse: (revisionId: string) => void;
}

type Filter = "all" | "built-in" | "custom" | "disabled";

type FormState =
  | { mode: "create" }
  | {
      mode: "edit";
      agent: AiAgentRevision & { systemInstruction?: string };
    };

export function AgentsWorkspace({
  projectId,
  copy,
  onUse,
}: AgentsWorkspaceProps) {
  const text = copy.agentsWorkspace;
  const isChinese = text.heading !== "Agents";
  const [filter, setFilter] = useState<Filter>("all");
  const [detailRevisionId, setDetailRevisionId] = useState<string | null>(null);
  const [formState, setFormState] = useState<FormState | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  const agentsQuery = useAgents(projectId);
  const skillsQuery = useSkills(projectId);
  const modelCatalog = useModelCatalog(projectId);
  const setEnabled = useSetAgentEnabled(projectId);

  const enabledSkills = useMemo(
    () => (skillsQuery.data ?? []).filter((item) => item.status === "enabled"),
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

  const visibleItems = useMemo(() => {
    if (filter === "built-in")
      return latestItems.filter(
        (item) => item.sourceKind === "built_in" && item.status === "enabled",
      );
    if (filter === "custom")
      return latestItems.filter(
        (item) => item.sourceKind !== "built_in" && item.status === "enabled",
      );
    if (filter === "disabled")
      return latestItems.filter((item) => item.status === "disabled");
    return latestItems;
  }, [filter, latestItems]);

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

      {visibleItems.length === 0 ? (
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
                  <span data-status={item.status}>{item.status}</span>
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
