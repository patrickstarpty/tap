import { Button, Input } from "antd";
import { useMemo, useState, type FormEvent } from "react";
import { useQueries } from "@tanstack/react-query";

import {
  AiAssetError,
  aiAssetKeys,
  fetchSkillRevisionDetail,
  useCreateAgent,
  useReviseAgent,
  type AiAgentRevisionDetail,
  type SkillRevision,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import type { ModelCatalogItem } from "../../../../features/knowledge/api/modelCatalog";
import { AccessibleDialog } from "../AccessibleDialog";
import type { WorkspaceCopy } from "../copy";
import {
  buildSkillMarkdown,
  SKILL_TURN_BUDGET_BYTES,
  utf8ByteLength,
} from "../skills/skillMarkdown";

// Selecting skills for an agent commits the project to bundling that much
// Skill text into every turn that uses it, so the authoring form warns at
// 90% and blocks saving past the limit — the same two-stage budget gate
// SkillForm applies to a single SKILL.md, just summed across the set. Kept
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
  const [skillRevisionIds, setSkillRevisionIds] = useState<readonly string[]>(
    agent?.skillRevisionIds ?? [],
  );
  const [skillQuery, setSkillQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const createAgent = useCreateAgent(projectId);
  const reviseAgent = useReviseAgent(projectId);
  const isSaving = createAgent.isPending || reviseAgent.isPending;

  const skillDetailQueries = useQueries({
    queries: skillRevisionIds.map((revisionId) => ({
      queryKey: aiAssetKeys.skill(projectId, revisionId),
      queryFn: () => fetchSkillRevisionDetail(projectId, revisionId),
      staleTime: 30_000,
    })),
  });
  const skillSizesPending = skillDetailQueries.some((query) => query.isPending);
  const skillSizesErrored = skillDetailQueries.some((query) => query.isError);

  const { totalBytes, largestSkills } = useMemo(() => {
    const sized = skillDetailQueries
      .map((query) => query.data)
      .filter((detail): detail is SkillRevisionDetail => detail !== undefined)
      .map((detail) => ({
        displayName: detail.displayName,
        bytes: utf8ByteLength(
          buildSkillMarkdown({
            name: detail.displayName,
            description: detail.description ?? "",
            instructions: detail.instructions ?? "",
          }),
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
  const budgetUnknown = skillRevisionIds.length > 0 && skillSizesPending;
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
  const canSave =
    (mode === "edit" || nameValid) &&
    descriptionValid &&
    systemInstructionValid &&
    !overBudget &&
    !budgetUnknown &&
    !skillSizesErrored;

  const toggleSkill = (revisionId: string) => {
    setSkillRevisionIds((current) =>
      current.includes(revisionId)
        ? current.filter((id) => id !== revisionId)
        : [...current, revisionId],
    );
  };

  const visibleSkills = useMemo(() => {
    const normalized = skillQuery.trim().toLocaleLowerCase();
    if (normalized.length === 0) return skills;
    return skills.filter((skill) =>
      skill.displayName.toLocaleLowerCase().includes(normalized),
    );
  }, [skillQuery, skills]);

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
            skillRevisionIds: [...skillRevisionIds],
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
          skillRevisionIds: [...skillRevisionIds],
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
        <fieldset>
          <legend>{form.skills}</legend>
          <Input
            aria-label={form.searchSkills}
            placeholder={form.searchSkills}
            value={skillQuery}
            onChange={(event) => setSkillQuery(event.target.value)}
          />
          {skills.length === 0 ? (
            <p>{form.noSkills}</p>
          ) : (
            visibleSkills.map((skill) => {
              // An Agent is only ever used for `knowledge.answer` turns, so
              // a Skill scoped to a different applicable task (e.g.
              // `test-plan.generate`) can't do anything there — gray it out
              // instead of letting it be attached and silently ignored.
              const notApplicable =
                !skill.applicableTasks.includes("knowledge.answer");
              return (
                <label key={skill.revisionId} aria-disabled={notApplicable}>
                  <input
                    type="checkbox"
                    checked={skillRevisionIds.includes(skill.revisionId)}
                    disabled={notApplicable}
                    onChange={() => toggleSkill(skill.revisionId)}
                  />
                  <span>{skill.displayName}</span>
                  <span className="tap-agent-skill-revision">
                    {skill.revisionId}
                  </span>
                </label>
              );
            })
          )}
        </fieldset>
        <div className="tap-agent-tools-readonly">
          <strong>{form.toolsLabel}</strong>
          <p>{form.toolsBuiltIn}</p>
        </div>
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
