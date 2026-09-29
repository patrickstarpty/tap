import type { CatalogDraft } from "./CatalogWorkspace";

export const CATALOG_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function isValidCatalogName(name: string): boolean {
  return CATALOG_NAME_PATTERN.test(name);
}

export function toSkillMarkdown(draft: CatalogDraft): string {
  return `---\nname: ${draft.name}\ndescription: ${draft.description}\n---\n\n${draft.instructions}\n`;
}
