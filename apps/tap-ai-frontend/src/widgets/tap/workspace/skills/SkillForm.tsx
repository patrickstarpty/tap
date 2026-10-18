import { Button, Input } from "antd";
import { useMemo, useState, type FormEvent } from "react";

import {
  AiAssetError,
  useCreateSkill,
  useReviseSkill,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import { AccessibleDialog } from "../AccessibleDialog";
import type { WorkspaceCopy } from "../copy";
import {
  SKILL_MD_BYTE_LIMIT,
  buildSkillMarkdown,
  utf8ByteLength,
} from "./skillMarkdown";

export type SkillApplicableTask =
  "knowledge.answer" | "test-plan.generate" | "automation.generate";

const APPLICABLE_TASKS: readonly SkillApplicableTask[] = [
  "knowledge.answer",
  "test-plan.generate",
  "automation.generate",
];

export interface SkillFormProps {
  projectId: string;
  copy: WorkspaceCopy;
  mode: "create" | "edit" | "copy";
  skill?: SkillRevisionDetail;
  onClose: () => void;
  onSaved?: (revisionId: string) => void;
  opener: HTMLElement | null;
}

const NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/**
 * The frontmatter `name` is the skill's real identity (what the backend
 * sets `display_name` from on revise/create) — `skillId`/`assetId` are
 * server-generated opaque ids, never the authored name. Falling back to
 * `skillId` here previously renamed every edited skill to its id.
 */
function skillFrontmatterName(
  skill: SkillRevisionDetail | undefined,
): string | null {
  const value = skill?.frontmatter?.name;
  return typeof value === "string" ? value : null;
}

export function SkillForm({
  projectId,
  copy,
  mode,
  skill,
  onClose,
  onSaved,
  opener,
}: SkillFormProps) {
  const form = copy.skillsWorkspace.form;
  const [name, setName] = useState(
    mode === "copy"
      ? ""
      : (skillFrontmatterName(skill) ?? skill?.displayName ?? ""),
  );
  const [description, setDescription] = useState(skill?.description ?? "");
  const [instructions, setInstructions] = useState(skill?.instructions ?? "");
  const [applicableTasks, setApplicableTasks] = useState<
    readonly SkillApplicableTask[]
  >(
    (skill?.applicableTasks as readonly SkillApplicableTask[] | undefined) ?? [
      "knowledge.answer",
    ],
  );
  const [error, setError] = useState<string | null>(null);

  const createSkill = useCreateSkill(projectId);
  const reviseSkill = useReviseSkill(projectId);
  const isSaving = createSkill.isPending || reviseSkill.isPending;

  const preview = useMemo(
    () => buildSkillMarkdown({ name, description, instructions }),
    [name, description, instructions],
  );
  const byteSize = useMemo(() => utf8ByteLength(preview), [preview]);
  const overBudget = byteSize > SKILL_MD_BYTE_LIMIT;

  const trimmedName = name.trim();
  const nameValid = NAME_PATTERN.test(trimmedName) && trimmedName.length <= 64;
  const descriptionValid = description.trim().length > 0;
  const instructionsValid = instructions.trim().length > 0;
  const canSave =
    (mode === "edit" || nameValid) &&
    descriptionValid &&
    instructionsValid &&
    !overBudget;

  const title =
    mode === "create"
      ? form.createTitle
      : mode === "copy"
        ? form.copyTitle
        : form.editTitle;

  const toggleTask = (task: SkillApplicableTask) => {
    setApplicableTasks((current) =>
      current.includes(task)
        ? current.filter((item) => item !== task)
        : [...current, task],
    );
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!canSave) return;
    try {
      if (mode === "edit" && skill !== undefined) {
        const result = await reviseSkill.mutateAsync({
          skillId: skill.skillId,
          request: {
            baseRevisionId: skill.revisionId,
            name: trimmedName,
            description: description.trim(),
            instructions,
            applicableTasks: [...applicableTasks],
          },
        });
        onSaved?.(result.revisionId);
      } else {
        const result = await createSkill.mutateAsync({
          name: trimmedName,
          description: description.trim(),
          instructions,
          applicableTasks: [...applicableTasks],
        });
        onSaved?.(result.revisionId);
      }
      onClose();
    } catch (cause) {
      if (cause instanceof AiAssetError && cause.code === "revision_conflict") {
        setError(form.revisionConflict);
      } else {
        setError(form.saveFailed);
      }
    }
  };

  return (
    <AccessibleDialog
      ariaLabel={title}
      className="tap-skill-form-dialog"
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
        <fieldset>
          <legend>{form.applicableTasks}</legend>
          {APPLICABLE_TASKS.map((task) => (
            <label key={task}>
              <input
                type="checkbox"
                checked={applicableTasks.includes(task)}
                onChange={() => toggleTask(task)}
              />
              <span>{copy.skillsWorkspace.taskLabels[task]}</span>
            </label>
          ))}
        </fieldset>
        <label>
          <span>{form.instructions}</span>
          <Input.TextArea
            aria-label={form.instructions}
            rows={10}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
          />
        </label>
        <div className="tap-catalog-markdown-preview">
          <strong>{form.preview}</strong>
          <pre>{preview}</pre>
          <p data-over-budget={overBudget}>
            {Math.round(byteSize / 1024)} KB /{" "}
            {Math.round(SKILL_MD_BYTE_LIMIT / 1024)} KB
          </p>
        </div>
        {overBudget ? <p role="alert">{form.budgetExceeded}</p> : null}
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
