import { CORE_SCHEMA, load } from "js-yaml";
import { CATALOG_NAME_PATTERN } from "./catalogMarkdown";

export interface SkillPreview {
  name: string;
  description: string;
  instructions: string;
  requestedTools: string[];
}
export async function previewSkillFile(file: File): Promise<SkillPreview> {
  if (file.name !== "SKILL.md" || file.size > 65536)
    throw new Error("Choose a UTF-8 SKILL.md file up to 64 KiB.");
  const bytes = await file.arrayBuffer();
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  return previewSkillMarkdown(text);
}
export function previewSkillMarkdown(text: string): SkillPreview {
  const match =
    /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)([\s\S]*)$/.exec(
      text.replace(/^\uFEFF/, ""),
    );
  if (!match)
    throw new Error("SKILL.md needs YAML front matter and instructions.");
  let depth = 0;
  const value = load(match[1], {
    schema: CORE_SCHEMA,
    listener(event, state) {
      depth += event === "open" ? 1 : -1;
      if (depth > 32 || (state as unknown as { anchor?: unknown }).anchor)
        throw new Error(
          "YAML anchors and deeply nested values are not supported.",
        );
    },
  });
  const seen = new Set<object>();
  const validate = (item: unknown, level = 0): void => {
    if (level > 16 || (typeof item === "number" && !Number.isFinite(item)))
      throw new Error("Front matter contains an unsupported value.");
    if (
      typeof item === "string" &&
      /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(
        item,
      )
    )
      throw new Error("Front matter must contain valid Unicode.");
    if (item && typeof item === "object") {
      if (seen.has(item)) throw new Error("YAML aliases are not supported.");
      seen.add(item);
      Object.values(item).forEach((child) => validate(child, level + 1));
      if (Object.hasOwn(item, "<<"))
        throw new Error("YAML merge keys are not supported.");
    }
  };
  validate(value);
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Front matter must be a mapping.");
  const data = value as Record<string, unknown>;
  if (
    typeof data.name !== "string" ||
    !CATALOG_NAME_PATTERN.test(data.name) ||
    data.name.length > 64 ||
    typeof data.description !== "string" ||
    !data.description.trim() ||
    data.description.length > 1024 ||
    !match[2].trim()
  )
    throw new Error(
      "Enter a lowercase kebab-case name, description and instructions.",
    );
  const rawTools = data["allowed-tools"] ?? [];
  const requestedTools =
    typeof rawTools === "string"
      ? rawTools.split(/[,\s]+/).filter(Boolean)
      : rawTools;
  if (
    !Array.isArray(requestedTools) ||
    requestedTools.some(
      (tool) =>
        typeof tool !== "string" || !tool.trim() || tool.trim().length > 128,
    ) ||
    requestedTools.length > 64
  )
    throw new Error("allowed-tools must be a list of tool names.");
  if (
    data.metadata !== undefined &&
    (!data.metadata ||
      typeof data.metadata !== "object" ||
      Array.isArray(data.metadata))
  )
    throw new Error("metadata must be a mapping.");
  return {
    name: data.name,
    description: data.description,
    instructions: match[2].trim(),
    requestedTools: [
      ...new Set((requestedTools as string[]).map((tool) => tool.trim())),
    ],
  };
}
