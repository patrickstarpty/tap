import { Button } from "antd";
import { useMemo, useRef, useState, type MouseEvent } from "react";

import {
  useSetSkillEnabled,
  useSkills,
  type SkillRevision,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import type { WorkspaceCopy } from "../copy";
import { SkillDetailDrawer } from "./SkillDetailDrawer";
import { SkillForm } from "./SkillForm";
import { SkillImportDialog } from "./SkillImportDialog";

export interface SkillsWorkspaceProps {
  projectId: string;
  copy: WorkspaceCopy;
  onUse: (revisionId: string) => void;
}

type Filter = "all" | "built-in" | "custom" | "disabled";

type FormState =
  | { mode: "create" }
  | { mode: "edit"; skill: SkillRevisionDetail }
  | { mode: "copy"; skill: SkillRevisionDetail };

export function SkillsWorkspace({
  projectId,
  copy,
  onUse,
}: SkillsWorkspaceProps) {
  const text = copy.skillsWorkspace;
  const isChinese = text.heading !== "Skills";
  const [filter, setFilter] = useState<Filter>("all");
  const [importOpen, setImportOpen] = useState(false);
  const [detailRevisionId, setDetailRevisionId] = useState<string | null>(null);
  const [formState, setFormState] = useState<FormState | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);

  const skillsQuery = useSkills(projectId);
  const setEnabled = useSetSkillEnabled(projectId);

  const latestItems = useMemo(
    () => (skillsQuery.data ?? []).filter((item) => item.isLatest),
    [skillsQuery.data],
  );

  const visibleItems = useMemo(() => {
    // "Built-in"/"Custom" are origin facets restricted to enabled items;
    // "Disabled" is a status facet spanning both origins. Each item lands
    // in exactly one of the three facets, matching the four-state filter
    // the brief specifies (All / Built-in / Custom / Disabled).
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

  const openImport = (event: MouseEvent<HTMLElement>) => {
    triggerRef.current = event.currentTarget;
    setImportOpen(true);
  };

  const openCreate = (event: MouseEvent<HTMLElement>) => {
    triggerRef.current = event.currentTarget;
    setFormState({ mode: "create" });
  };

  const openDetail = (item: SkillRevision, event: MouseEvent<HTMLElement>) => {
    triggerRef.current = event.currentTarget;
    setDetailRevisionId(item.revisionId);
  };

  return (
    <section
      className="tap-module tap-skills-workspace"
      aria-labelledby="skills-heading"
    >
      <header className="tap-module-heading">
        <div>
          <h1 id="skills-heading">{text.heading}</h1>
          <p>{text.description}</p>
        </div>
        <div className="tap-skills-header-actions">
          <Button onClick={openImport}>{text.importSkill}</Button>
          <Button type="primary" onClick={openCreate}>
            {text.createSkill}
          </Button>
        </div>
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
                  <span data-status={item.status}>
                    {item.status === "enabled"
                      ? text.statusEnabled
                      : text.statusDisabled}
                  </span>
                </div>
                {item.description ? <p>{item.description}</p> : null}
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
                      skillId: item.skillId,
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

      {importOpen ? (
        <SkillImportDialog
          projectId={projectId}
          copy={copy}
          onClose={() => setImportOpen(false)}
          opener={triggerRef.current}
        />
      ) : null}

      {detailRevisionId !== null ? (
        <SkillDetailDrawer
          projectId={projectId}
          copy={copy}
          revisionId={detailRevisionId}
          onClose={() => setDetailRevisionId(null)}
          onUse={onUse}
          onEdit={(detail) => {
            setDetailRevisionId(null);
            setFormState({ mode: "edit", skill: detail });
          }}
          onCopyAsCustom={(detail) => {
            setDetailRevisionId(null);
            setFormState({ mode: "copy", skill: detail });
          }}
          opener={triggerRef.current}
        />
      ) : null}

      {formState !== null ? (
        <SkillForm
          projectId={projectId}
          copy={copy}
          mode={formState.mode}
          skill={formState.mode === "create" ? undefined : formState.skill}
          onClose={() => setFormState(null)}
          opener={triggerRef.current}
        />
      ) : null}
    </section>
  );
}
