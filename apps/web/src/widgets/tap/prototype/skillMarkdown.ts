import type { CatalogDraft } from "./CatalogWorkspace";

export const CATALOG_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function isValidCatalogName(name: string): boolean {
  return CATALOG_NAME_PATTERN.test(name);
}

function toYamlDoubleQuotedString(value: string): string {
  const escaped = value
    .replaceAll("\\", "\\\\")
    .replaceAll('"', '\\"')
    .replaceAll("\n", "\\n");
  return `"${escaped}"`;
}

export function toSkillMarkdown(draft: CatalogDraft): string {
  const description = toYamlDoubleQuotedString(draft.description);
  return `---\nname: ${draft.name}\ndescription: ${description}\n---\n\n${draft.instructions}\n`;
}
