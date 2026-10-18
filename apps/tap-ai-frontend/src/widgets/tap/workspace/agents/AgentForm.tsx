import { Button, Input } from "antd";
import { useMemo, useState, type FormEvent } from "react";
import { useQueries } from "@tanstack/react-query";

import {
  AiAssetError,
  useCreateAgent,
  useReviseAgent,
  type AiAgentRevision,
  type SkillRevision,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import type { ModelCatalogItem } from "../../../../features/knowledge/api/modelCatalog";
import { AccessibleDialog } from "../AccessibleDialog";
import type { WorkspaceCopy } from "../copy";
import { buildSkillMarkdown, utf8ByteLength } from "../skills/skillMarkdown";

// Global constraint (global-constraints.md): "单轮 Skill 文本（含附加参考）合计 ≤ 96 KB".
// Selecting skills for an agent commits the project to bundling that much
// Skill text into every turn that uses it, so the authoring form warns at
// 90% and blocks saving past the limit — the same two-stage budget gate
// SkillForm applies to a single SKILL.md, just summed across the set.
export const AGENT_SKILLS_BUDGET_BYTES = 96 * 1024;
const AGENT_SKILLS_WARNING_RATIO = 0.9;

export interface AgentFormProps {
  projectId: string;
  copy: WorkspaceCopy;
  mode: "create" | "edit";
  // The list/detail `GET` routes only ever return `AiAgentRevisionSummary`
  // (no `systemInstruction` — see ai_get_agent_revision's contract); only a
  // create/revise response carries it. `systemInstruction` is therefore
  // optional here and the field starts blank when editing from a Summary.
  agent?: AiAgentRevision & { systemInstruction?: string };
  skills: readonly SkillRevision[];
  models: readonly ModelCatalogItem[];
  onClose: () => void;
  onSaved?: (revisionId: string) => void;
  opener: HTMLElement | null;
}

function skillRevisionDetailQueryKey(projectId: string, revisionId: string) {
  return ["ai-assets", projectId, "skill", revisionId] as const;
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
  const [name, setName] = useState(agent?.displayName ?? "");
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
  const [error, setError] = useState<string | null>(null);

  const createAgent = useCreateAgent(projectId);
  const reviseAgent = useReviseAgent(projectId);
  const isSaving = createAgent.isPending || reviseAgent.isPending;

  const skillDetailQueries = useQueries({
    queries: skillRevisionIds.map((revisionId) => ({
      queryKey: skillRevisionDetailQueryKey(projectId, revisionId),
      queryFn: () => fetchSkillRevisionDetail(projectId, revisionId),
      staleTime: 30_000,
    })),
  });

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

  const overBudget = totalBytes > AGENT_SKILLS_BUDGET_BYTES;
  const nearBudget =
    !overBudget &&
    totalBytes >= AGENT_SKILLS_BUDGET_BYTES * AGENT_SKILLS_WARNING_RATIO;

  const trimmedName = name.trim();
  const nameValid = trimmedName.length > 0 && trimmedName.length <= 128;
  const descriptionValid = description.trim().length > 0;
  const systemInstructionValid = systemInstruction.trim().length > 0;
  const canSave =
    (mode === "edit" || nameValid) &&
    descriptionValid &&
    systemInstructionValid &&
    !overBudget;

  const toggleSkill = (revisionId: string) => {
    setSkillRevisionIds((current) =>
      current.includes(revisionId)
        ? current.filter((id) => id !== revisionId)
        : [...current, revisionId],
    );
  };

  const title = mode === "create" ? form.createTitle : form.editTitle;

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
            description: description.trim(),
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
          description: description.trim(),
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
      className="tap-agent-form-dialog"
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
        <label>
          <span>{form.systemInstruction}</span>
          <Input.TextArea
            aria-label={form.systemInstruction}
            rows={10}
            value={systemInstruction}
            onChange={(event) => setSystemInstruction(event.target.value)}
          />
        </label>
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
          {skills.length === 0 ? (
            <p>{form.noSkills}</p>
          ) : (
            skills.map((skill) => {
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
                </label>
              );
            })
          )}
        </fieldset>
        <div className="tap-agent-tools-readonly">
          <strong>{form.toolsLabel}</strong>
          <p>{form.toolsBuiltIn}</p>
        </div>
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
