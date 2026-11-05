import { Button, Input } from "antd";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useQueries } from "@tanstack/react-query";

import {
  AiAssetError,
  aiAssetKeys,
  fetchSkillRevisionDetail,
  useCreateAgent,
  useReviseAgent,
  useTools,
  type AiAgentRevisionDetail,
  type SkillRevision,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import type { ModelCatalogItem } from "../../../../features/knowledge/api/modelCatalog";
import { AccessibleDialog } from "../AccessibleDialog";
import type { WorkspaceCopy } from "../copy";
import {
  SKILL_TURN_BUDGET_BYTES,
  utf8ByteLength,
} from "../skills/skillMarkdown";

// Pinning skills on an agent commits the project to loading that much
// Skill text (each pinned Skill's latest revision) into every turn that
// uses it, so the authoring form warns at 90% and blocks saving past the
// limit — the same two-stage budget gate SkillForm applies to a single
// SKILL.md, just summed across the pinned set. Kept
// as a re-export so existing imports of `AGENT_SKILLS_BUDGET_BYTES` from
// this module don't need to change.
export const AGENT_SKILLS_BUDGET_BYTES = SKILL_TURN_BUDGET_BYTES;
const AGENT_SKILLS_WARNING_RATIO = 0.9;

// Global constraint: "Agent：名称 ≤ 128 字符…描述 ≤ 1 024 字符；系统提示词必填 ≤ 32 KB."
const DESCRIPTION_MAX_LENGTH = 1024;
const SYSTEM_INSTRUCTION_MAX_BYTES = 32 * 1024;

export interface AgentFormProps {
  projectId: string;
  copy: WorkspaceCopy;
  mode: "create" | "edit" | "copy";
  agent?: AiAgentRevisionDetail;
  /** The latest revision of every Skill in the library, any status: only
   * enabled ones are offered as picks, a disabled one is still named (and
   * removable) when the Agent already pins or excludes it. */
  skills: readonly SkillRevision[];
  models: readonly ModelCatalogItem[];
  onClose: () => void;
  onSaved?: (revisionId: string) => void;
  opener: HTMLElement | null;
}

export function AgentForm({
  projectId,
  copy,
  mode,
  agent,
  skills,
  models,
  onClose,
  onSaved,
  opener,
}: AgentFormProps) {
  const form = copy.agentsWorkspace.form;
  // "Copy as custom" starts from a built-in Agent's fields but needs a new,
  // unique name — never prefilled from the built-in's own (its name is
  // already taken).
  const [name, setName] = useState(
    mode === "copy" ? "" : (agent?.displayName ?? ""),
  );
  const [description, setDescription] = useState(agent?.description ?? "");
  const [systemInstruction, setSystemInstruction] = useState(
    agent?.systemInstruction ?? "",
  );
  const [defaultModelAlias, setDefaultModelAlias] = useState(
    agent?.defaultModelAlias ?? "",
  );
  // By Skill, not by revision: a pinned Skill always loads its latest
  // enabled revision, so there is nothing to upgrade.
  const [pinnedSkillIds, setPinnedSkillIds] = useState<readonly string[]>(
    agent?.pinnedSkillIds ?? [],
  );
  const [excludedSkillIds, setExcludedSkillIds] = useState<readonly string[]>(
    agent?.excludedSkillIds ?? [],
  );
  const [skillQuery, setSkillQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  // `null` means "not yet decided": an edit/copy starts from the base
  // Agent's own revised allowlist (set eagerly below, no tools fetch
  // needed); a plain create (no base Agent) waits for `GET /ai/tools` so
  // the initial checked set can be `required || defaultEnabled`, per
  // tool, rather than guessing.
  const [toolIds, setToolIds] = useState<readonly string[] | null>(
    agent?.toolAllowlist ?? null,
  );

  const createAgent = useCreateAgent(projectId);
  const reviseAgent = useReviseAgent(projectId);
  const isSaving = createAgent.isPending || reviseAgent.isPending;
  // Reuses the same locale-from-copy trick `AgentsWorkspace` applies
  // (`text.heading !== "Agents"`) -- `AgentForm` is only ever given a
  // `WorkspaceCopy`, not a raw locale.
  const toolsLocale = copy.agentsWorkspace.heading === "Agents" ? "en" : "zh";
  const tools = useTools(projectId, toolsLocale);

  // Only a brand-new Agent (no base revision to start from) needs the
  // fetched tool catalog's `defaultEnabled` flags to seed its initial
  // selection -- an edit/copy already has `toolIds` set from the base
  // Agent's `toolAllowlist` above and must not have it overwritten once
  // the catalog loads. Runs as an effect, not during render (review fix
  // round 1, folded minor): a render-phase `setState` call works here (the
  // `toolIds === null` guard prevents an infinite loop) but is the wrong
  // tool for "derive state once an async query resolves" — an effect
  // keeps the render itself pure and matches how every other async-derived
  // field in this form (e.g. Skill budgets below) is computed.
  useEffect(() => {
    if (toolIds !== null || tools.data === undefined) return;
    setToolIds(
      tools.data
        .filter((tool) => tool.required || tool.defaultEnabled)
        .map((tool) => tool.toolId),
    );
  }, [toolIds, tools.data]);

  const toggleTool = (toolId: string) => {
    setToolIds((current) => {
      const base = current ?? [];
      return base.includes(toolId)
        ? base.filter((id) => id !== toolId)
        : [...base, toolId];
    });
  };

  // Required tools are always checked and disabled regardless of
  // `toolIds` (a defensive union, not a trust assumption): the submitted
  // set always includes every required tool id even if `toolIds` was
  // somehow missing one.
  const requiredToolIds = (tools.data ?? [])
    .filter((tool) => tool.required)
    .map((tool) => tool.toolId);
  const submittedToolIds = Array.from(
    new Set([...requiredToolIds, ...(toolIds ?? [])]),
  );

  const skillById = useMemo(
    () => new Map(skills.map((skill) => [skill.skillId, skill])),
    [skills],
  );
  const enabledSkills = useMemo(
    () => skills.filter((skill) => skill.status === "enabled"),
    [skills],
  );
  // The budget sums each pinned Skill's latest enabled revision -- the one
  // a Turn would freeze. A disabled pinned Skill is skipped at answer time,
  // so it doesn't count.
  const pinnedRevisionIds = pinnedSkillIds
    .map((skillId) => skillById.get(skillId))
    .filter(
      (skill): skill is SkillRevision =>
        skill !== undefined && skill.status === "enabled",
    )
    .map((skill) => skill.revisionId);
  const skillDetailQueries = useQueries({
    queries: pinnedRevisionIds.map((revisionId) => ({
      queryKey: aiAssetKeys.skill(projectId, revisionId),
      queryFn: () => fetchSkillRevisionDetail(projectId, revisionId),
      staleTime: 30_000,
    })),
  });
  const skillSizesPending = skillDetailQueries.some((query) => query.isPending);
  const skillSizesErrored = skillDetailQueries.some((query) => query.isError);

  const { totalBytes, largestSkills } = useMemo(() => {
    // Match the server's per-turn budget accounting exactly (I-5,
    // `skill_selection.py::_skill_text_bytes`): instructions plus attached
    // reference text, not a re-derived SKILL.md (which would also count
    // frontmatter/name/description bytes the server never bills this
    // budget for).
    const sized = skillDetailQueries
      .map((query) => query.data)
      .filter((detail): detail is SkillRevisionDetail => detail !== undefined)
      .map((detail) => ({
        displayName: detail.displayName,
        bytes: utf8ByteLength(
          (detail.instructions ?? "") + (detail.attachedReferences ?? ""),
        ),
      }));
    const total = sized.reduce((sum, item) => sum + item.bytes, 0);
    const largest = [...sized]
      .sort((a, b) => b.bytes - a.bytes)
      .slice(0, 3)
      .map((item) => item.displayName);
    return { totalBytes: total, largestSkills: largest };
  }, [skillDetailQueries]);

  // Until every selected Skill's size has resolved, the real total is
  // unknown — treating that as "0 bytes used" would let Save enable before
  // the budget is actually checked, so it's folded into `canSave` below
  // rather than only gating on `overBudget`.
  const budgetUnknown = pinnedRevisionIds.length > 0 && skillSizesPending;
  const overBudget = totalBytes > AGENT_SKILLS_BUDGET_BYTES;
  const nearBudget =
    !overBudget &&
    !budgetUnknown &&
    totalBytes >= AGENT_SKILLS_BUDGET_BYTES * AGENT_SKILLS_WARNING_RATIO;

  const trimmedName = name.trim();
  const trimmedDescription = description.trim();
  const systemInstructionBytes = utf8ByteLength(systemInstruction);
  const nameValid = trimmedName.length > 0 && trimmedName.length <= 128;
  const descriptionValid =
    trimmedDescription.length > 0 &&
    trimmedDescription.length <= DESCRIPTION_MAX_LENGTH;
  const systemInstructionValid =
    systemInstruction.trim().length > 0 &&
    systemInstructionBytes <= SYSTEM_INSTRUCTION_MAX_BYTES;
  // I-6: a pinned Skill becoming disabled must not be a permanent save
  // dead-end -- it is surfaced below with a warning and a way to remove
  // it, not treated as a blocking error.
  const canSave =
    (mode === "edit" || nameValid) &&
    descriptionValid &&
    systemInstructionValid &&
    !overBudget &&
    !budgetUnknown &&
    toolIds !== null;

  const visibleSkills = useMemo(() => {
    const normalized = skillQuery.trim().toLocaleLowerCase();
    if (normalized.length === 0) return enabledSkills;
    return enabledSkills.filter((skill) =>
      skill.displayName.toLocaleLowerCase().includes(normalized),
    );
  }, [skillQuery, enabledSkills]);

  const pickers = [
    {
      key: "pinned",
      legend: form.pinnedSkills,
      hint: form.pinnedSkillsHint,
      selected: pinnedSkillIds,
      setSelected: setPinnedSkillIds,
      // A Skill can't be both pinned and excluded (the server rejects it
      // with `agent_skill_conflict`), so each picker locks the other's picks.
      other: excludedSkillIds,
    },
    {
      key: "excluded",
      legend: form.excludedSkills,
      hint: form.excludedSkillsHint,
      selected: excludedSkillIds,
      setSelected: setExcludedSkillIds,
      other: pinnedSkillIds,
    },
  ] as const;

  const title =
    mode === "create"
      ? form.createTitle
      : mode === "copy"
        ? form.copyTitle
        : form.editTitle;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!canSave) return;
    try {
      if (mode === "edit" && agent !== undefined) {
        const result = await reviseAgent.mutateAsync({
          agentId: agent.agentId,
          request: {
            baseRevisionId: agent.revisionId,
            name: trimmedName,
            description: trimmedDescription,
            systemInstruction,
            defaultModelAlias:
              defaultModelAlias.length > 0 ? defaultModelAlias : null,
            pinnedSkillIds: [...pinnedSkillIds],
            excludedSkillIds: [...excludedSkillIds],
            toolIds: submittedToolIds,
          },
        });
        onSaved?.(result.revisionId);
      } else {
        const result = await createAgent.mutateAsync({
          name: trimmedName,
          description: trimmedDescription,
          systemInstruction,
          defaultModelAlias:
            defaultModelAlias.length > 0 ? defaultModelAlias : null,
          pinnedSkillIds: [...pinnedSkillIds],
          excludedSkillIds: [...excludedSkillIds],
          toolIds: submittedToolIds,
        });
        onSaved?.(result.revisionId);
      }
      onClose();
    } catch (cause) {
      if (
        cause instanceof AiAssetError &&
        cause.code === "agent_name_conflict"
      ) {
        setError(form.nameConflict);
      } else if (
        cause instanceof AiAssetError &&
        cause.code === "revision_conflict"
      ) {
        setError(form.revisionConflict);
      } else if (
        cause instanceof AiAssetError &&
        cause.code === "agent_skill_conflict"
      ) {
        setError(form.skillConflict);
      } else if (
        cause instanceof AiAssetError &&
        cause.code === "agent_skill_limit_exceeded"
      ) {
        setError(form.skillLimitExceeded);
      } else {
        setError(form.saveFailed);
      }
    }
  };

  return (
    <AccessibleDialog
      ariaLabel={title}
      className="tap-agent-form-dialog tap-catalog-dialog"
      onClose={onClose}
      opener={opener}
    >
      <header>
        <h2>{title}</h2>
      </header>
      <form onSubmit={submit}>
        <label>
          <span>{form.name}</span>
          <Input
            aria-label={form.name}
            value={name}
            disabled={mode === "edit"}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        {!nameValid && trimmedName.length > 0 ? (
          <p role="alert">{form.nameInvalid}</p>
        ) : null}
        <label>
          <span>{form.description}</span>
          <Input.TextArea
            aria-label={form.description}
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <p data-over-limit={trimmedDescription.length > DESCRIPTION_MAX_LENGTH}>
          {trimmedDescription.length} / {DESCRIPTION_MAX_LENGTH}
        </p>
        {trimmedDescription.length > DESCRIPTION_MAX_LENGTH ? (
          <p role="alert">{form.descriptionTooLong}</p>
        ) : null}
        <label>
          <span>{form.systemInstruction}</span>
          <Input.TextArea
            aria-label={form.systemInstruction}
            rows={10}
            value={systemInstruction}
            onChange={(event) => setSystemInstruction(event.target.value)}
          />
        </label>
        <p
          data-over-limit={
            systemInstructionBytes > SYSTEM_INSTRUCTION_MAX_BYTES
          }
        >
          {Math.round(systemInstructionBytes / 1024)} KB /{" "}
          {Math.round(SYSTEM_INSTRUCTION_MAX_BYTES / 1024)} KB
        </p>
        {systemInstructionBytes > SYSTEM_INSTRUCTION_MAX_BYTES ? (
          <p role="alert">{form.systemInstructionTooLarge}</p>
        ) : null}
        <label>
          <span>{form.defaultModel}</span>
          <select
            aria-label={form.defaultModel}
            value={defaultModelAlias}
            onChange={(event) => setDefaultModelAlias(event.target.value)}
          >
            <option value="">{form.defaultModelGlobal}</option>
            {models.map((model) => (
              <option key={model.alias} value={model.alias}>
                {model.displayName}
              </option>
            ))}
          </select>
        </label>
        <Input
          aria-label={form.searchSkills}
          placeholder={form.searchSkills}
          value={skillQuery}
          onChange={(event) => setSkillQuery(event.target.value)}
        />
        {pickers.map((picker) => {
          // A pinned/excluded Skill that is disabled now is not offered as
          // a pick, so it would otherwise be an invisible, unremovable
          // entry -- list it with a warning and a way to remove it.
          const unavailable = picker.selected.filter(
            (skillId) => skillById.get(skillId)?.status !== "enabled",
          );
          return (
            <fieldset key={picker.key}>
              <legend>{picker.legend}</legend>
              <p className="tap-agent-skill-hint">{picker.hint}</p>
              {enabledSkills.length === 0 ? (
                <p>{form.noSkills}</p>
              ) : (
                visibleSkills.map((skill) => {
                  // An Agent is only ever used for `knowledge.answer` turns,
                  // so a Skill scoped to a different applicable task (e.g.
                  // `test-plan.generate`) can't do anything there — gray it
                  // out instead of letting it be picked and silently ignored.
                  const notApplicable =
                    !skill.applicableTasks.includes("knowledge.answer");
                  const lockedByOther = picker.other.includes(skill.skillId);
                  const checked = picker.selected.includes(skill.skillId);
                  return (
                    <label
                      key={skill.skillId}
                      aria-disabled={notApplicable || lockedByOther}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={notApplicable || lockedByOther}
                        onChange={() =>
                          picker.setSelected((current) =>
                            checked
                              ? current.filter((id) => id !== skill.skillId)
                              : [...current, skill.skillId],
                          )
                        }
                      />
                      <span>{skill.displayName}</span>
                    </label>
                  );
                })
              )}
              {unavailable.length > 0 ? (
                <div className="tap-agent-skills-unavailable">
                  {unavailable.map((skillId) => (
                    <div key={skillId} role="alert" aria-disabled>
                      <span>
                        {skillById.get(skillId)?.displayName ?? skillId}
                      </span>
                      <p>{form.skillDisabled}</p>
                      <Button
                        size="small"
                        onClick={() =>
                          picker.setSelected((current) =>
                            current.filter((id) => id !== skillId),
                          )
                        }
                      >
                        {form.removeSkill}
                      </Button>
                    </div>
                  ))}
                </div>
              ) : null}
            </fieldset>
          );
        })}
        <fieldset>
          <legend>{form.toolsLabel}</legend>
          {tools.isError ? <p role="alert">{form.toolsLoadFailed}</p> : null}
          {(tools.data ?? []).map((tool) => {
            const checked =
              tool.required || (toolIds ?? []).includes(tool.toolId);
            return (
              <label key={tool.toolId}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={tool.required}
                  onChange={() => toggleTool(tool.toolId)}
                />
                <span>{tool.name}</span>
              </label>
            );
          })}
        </fieldset>
        {budgetUnknown ? <p role="status">{form.budgetCalculating}</p> : null}
        {skillSizesErrored ? (
          <p role="alert">{form.budgetUnavailable}</p>
        ) : null}
        {nearBudget ? <p role="alert">{form.budgetWarning}</p> : null}
        {overBudget ? (
          <div role="alert">
            <p>{form.budgetExceeded}</p>
            <p>
              {form.largestSkills}: {largestSkills.join(", ")}
            </p>
          </div>
        ) : null}
        {error !== null ? <p role="alert">{error}</p> : null}
        <div className="tap-dialog-actions">
          <Button onClick={onClose}>{form.cancel}</Button>
          <Button
            type="primary"
            htmlType="submit"
            loading={isSaving}
            disabled={!canSave || isSaving}
          >
            {form.save}
          </Button>
        </div>
      </form>
    </AccessibleDialog>
  );
}
