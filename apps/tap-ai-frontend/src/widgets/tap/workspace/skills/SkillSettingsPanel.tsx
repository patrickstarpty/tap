import { Button } from "antd";
import { useState } from "react";

import {
  AiAssetError,
  useSetSkillEnabled,
} from "../../../../features/knowledge/api/aiAssets";
import {
  useTransferSkillOwner,
  useUpdateSkillProfile,
  type SkillCategory,
  type SkillHubDetail,
} from "../../../../features/knowledge/api/skillHub";
import type { WorkspaceCopy } from "../copy";

export interface SkillSettingsPanelProps {
  projectId: string;
  detail: SkillHubDetail;
  copy: WorkspaceCopy;
  /** Starts a new revision of the latest revision; omitted when the viewed
   * revision cannot be revised from here. */
  onEdit?: () => void;
}

const CATEGORIES: SkillCategory[] = [
  "test-design",
  "automation",
  "knowledge",
  "analysis",
  "writing",
  "other",
];

/** Category, owner transfer, enable/disable and a new revision. Built-in
 * Skills are maintained by Tapper: only enable/disable stays writable. */
export function SkillSettingsPanel({
  projectId,
  detail,
  copy,
  onEdit,
}: SkillSettingsPanelProps) {
  const text = copy.skillHub.settings;
  const hub = copy.skillHub;
  const skill = detail.skill;
  const readOnly = skill.builtIn;

  const profile = useUpdateSkillProfile(projectId);
  const transfer = useTransferSkillOwner(projectId);
  const setEnabled = useSetSkillEnabled(projectId);
  const [newOwner, setNewOwner] = useState("");
  const enabled = skill.status === "enabled";
  // Hold the chosen category while it saves and until the refetched
  // detail catches up; a failed save falls back to the stored one.
  const shownCategory =
    (profile.isPending || profile.isSuccess
      ? profile.variables?.category
      : undefined) ?? skill.category;

  const transferError =
    transfer.error === null
      ? null
      : transfer.error instanceof AiAssetError &&
          transfer.error.code === "actor_not_found"
        ? text.actorNotFound
        : text.transferFailed;

  return (
    <div className="tap-skill-settings">
      {readOnly ? (
        <p className="tap-skill-settings-note">{text.builtInReadOnly}</p>
      ) : null}

      <section aria-labelledby="skill-settings-category">
        <h3 id="skill-settings-category">{text.category}</h3>
        {readOnly ? (
          <p>{hub.categories[skill.category]}</p>
        ) : (
          <>
            <select
              aria-label={text.category}
              value={shownCategory}
              disabled={profile.isPending}
              onChange={(event) =>
                profile.mutate({
                  skillId: skill.skillId,
                  category: event.target.value as SkillCategory,
                })
              }
            >
              {CATEGORIES.map((category) => (
                <option key={category} value={category}>
                  {hub.categories[category]}
                </option>
              ))}
            </select>
            {profile.isError ? <p role="alert">{text.categoryFailed}</p> : null}
          </>
        )}
      </section>

      <section aria-labelledby="skill-settings-owner">
        <h3 id="skill-settings-owner">{text.owner}</h3>
        <p>{skill.ownerActorId}</p>
        {readOnly ? null : (
          <form
            className="tap-skill-settings-row"
            onSubmit={(event) => {
              event.preventDefault();
              const owner = newOwner.trim();
              if (owner === "") return;
              transfer.mutate(
                { skillId: skill.skillId, newOwnerActorId: owner },
                { onSuccess: () => setNewOwner("") },
              );
            }}
          >
            <input
              type="text"
              aria-label={text.newOwner}
              placeholder={text.newOwnerPlaceholder}
              value={newOwner}
              onChange={(event) => {
                setNewOwner(event.target.value);
                if (transfer.isError || transfer.isSuccess) transfer.reset();
              }}
            />
            <Button
              htmlType="submit"
              disabled={newOwner.trim() === ""}
              loading={transfer.isPending}
            >
              {text.transfer}
            </Button>
          </form>
        )}
        {transferError === null ? null : <p role="alert">{transferError}</p>}
        {transfer.isSuccess ? <p role="status">{text.transferred}</p> : null}
      </section>

      <section aria-labelledby="skill-settings-status">
        <h3 id="skill-settings-status">{text.status}</h3>
        <div className="tap-skill-settings-row">
          <span>{hub.statuses[skill.status]}</span>
          <Button
            loading={setEnabled.isPending}
            onClick={() =>
              setEnabled.mutate({ skillId: skill.skillId, enabled: !enabled })
            }
          >
            {enabled
              ? copy.skillsWorkspace.disable
              : copy.skillsWorkspace.enable}
          </Button>
        </div>
        {setEnabled.isError ? <p role="alert">{text.statusFailed}</p> : null}
      </section>

      {readOnly ? null : (
        <section aria-labelledby="skill-settings-revision">
          <h3 id="skill-settings-revision">{text.revision}</h3>
          {skill.sourceKind === "imported" ? (
            // Editing an imported package in place would drop its other
            // files; a new revision comes from re-importing it.
            <p>{copy.skillsWorkspace.editUnavailableImported}</p>
          ) : onEdit === undefined ? null : (
            <Button onClick={onEdit}>{text.newRevision}</Button>
          )}
        </section>
      )}
    </div>
  );
}
