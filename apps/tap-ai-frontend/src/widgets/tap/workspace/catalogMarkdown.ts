import type { CatalogKind } from "./model";

export const CATALOG_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function catalogMarkdown(
  kind: CatalogKind,
  draft: { name: string; description: string; instructions: string },
) {
  const name = draft.name.trim();
  const description = draft.description.trim();
  const instructions = draft.instructions.trim();
  if (
    name.length > 64 ||
    !CATALOG_NAME_PATTERN.test(name) ||
    description.length === 0 ||
    description.length > 1024 ||
    instructions.length === 0
  ) {
    throw new Error(
      "A kebab-case name, description, and Markdown instructions are required.",
    );
  }
  return {
    filename: kind === "skill" ? "SKILL.md" : `${name}.md`,
    content: `---\nname: ${name}\ndescription: ${JSON.stringify(description)}\n---\n\n${instructions}\n`,
  };
}
