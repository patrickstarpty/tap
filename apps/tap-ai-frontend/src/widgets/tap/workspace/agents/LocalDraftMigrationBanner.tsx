import { Button } from "antd";
import { useState } from "react";

import {
  useCreateAgent,
  useCreateSkill,
} from "../../../../features/knowledge/api/aiAssets";
import type { WorkspaceCopy } from "../copy";
import type { CatalogItem, CatalogKind } from "../model";

export interface LocalDraftMigrationBannerProps {
  projectId: string;
  kind: CatalogKind;
  copy: WorkspaceCopy;
}

function draftsStorageKey(projectId: string, kind: CatalogKind): string {
  return `tap-md-drafts-v1:${projectId}:${kind}`;
}

/** Same shape CatalogWorkspace used to persist local Markdown drafts under
 * (kebab-case name, non-empty description/instructions, origin "custom"),
 * so drafts saved before this workspace existed are still recognized. */
function readDrafts(projectId: string, kind: CatalogKind): CatalogItem[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const value = JSON.parse(
      localStorage.getItem(draftsStorageKey(projectId, kind)) ?? "[]",
    ) as unknown;
    if (!Array.isArray(value)) return [];
    return value.filter(
      (item): item is CatalogItem =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as CatalogItem).id === "string" &&
        (item as CatalogItem).kind === kind &&
        (item as CatalogItem).origin === "custom" &&
        typeof (item as CatalogItem).name === "string" &&
        typeof (item as CatalogItem).description === "string" &&
        typeof (item as CatalogItem).instructions === "string",
    );
  } catch {
    return [];
  }
}

function writeDrafts(
  projectId: string,
  kind: CatalogKind,
  drafts: readonly CatalogItem[],
): void {
  if (typeof localStorage === "undefined") return;
  if (drafts.length === 0) {
    localStorage.removeItem(draftsStorageKey(projectId, kind));
    return;
  }
  localStorage.setItem(
    draftsStorageKey(projectId, kind),
    JSON.stringify(drafts),
  );
}

/** Offers to import (or dismiss) Markdown drafts a person created in the
 * retired local-draft catalog, before any approved Agent/Skill authoring
 * API existed. Reads and clears the same `tap-md-drafts-v1:{projectId}:
 * {kind}` localStorage entry that catalog wrote. */
export function LocalDraftMigrationBanner({
  projectId,
  kind,
  copy,
}: LocalDraftMigrationBannerProps) {
  const text = copy.migrationBanner;
  const [drafts, setDrafts] = useState<readonly CatalogItem[]>(() =>
    readDrafts(projectId, kind),
  );
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  const createAgent = useCreateAgent(projectId);
  const createSkill = useCreateSkill(projectId);

  if (drafts.length === 0) return null;

  const removeDraft = (id: string) => {
    setDrafts((current) => {
      const next = current.filter((item) => item.id !== id);
      writeDrafts(projectId, kind, next);
      return next;
    });
  };

  const importDraft = async (item: CatalogItem) => {
    setPendingId(item.id);
    setFailedId(null);
    try {
      if (kind === "agent") {
        await createAgent.mutateAsync({
          name: item.name,
          description: item.description,
          systemInstruction: item.instructions,
        });
      } else {
        await createSkill.mutateAsync({
          name: item.name,
          description: item.description,
          instructions: item.instructions,
          applicableTasks: ["knowledge.answer"],
        });
      }
      removeDraft(item.id);
    } catch {
      setFailedId(item.id);
    } finally {
      setPendingId(null);
    }
  };

  return (
    <div
      className="tap-migration-banner"
      role="region"
      aria-label={text.heading}
    >
      <p>
        <strong>{text.heading}</strong>{" "}
        {kind === "agent" ? text.descriptionAgent : text.descriptionSkill}
      </p>
      <ul>
        {drafts.map((item) => (
          <li key={item.id}>
            <span>{item.name}</span>
            {failedId === item.id ? (
              <p role="alert">{text.importFailed}</p>
            ) : null}
            <Button
              type="primary"
              loading={pendingId === item.id}
              onClick={() => void importDraft(item)}
            >
              {text.import}
            </Button>
            <Button
              disabled={pendingId === item.id}
              onClick={() => removeDraft(item.id)}
            >
              {text.dismiss}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
