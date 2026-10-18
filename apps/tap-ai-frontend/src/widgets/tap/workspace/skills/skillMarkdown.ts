// Pure helpers for the Skill authoring form's live SKILL.md preview and the
// 256 KB per-file budget the backend enforces (global-constraints.md: "单个
// SKILL.md ≤ 256 KB 且 UTF-8"). Kept framework-free so the byte count — which
// must match the backend's UTF-8 byte count, not the JS string length — is
// trivial to unit test without rendering anything.

export const SKILL_MD_BYTE_LIMIT = 256 * 1024;

// Global constraint (global-constraints.md): "单轮 Skill 文本（含附加参考）合计
// ≤ 96 KB" — the server-enforced per-turn budget across every Skill a turn
// brings in (an Agent's bundled Skills plus any manually added), shared by
// the Agent authoring form and the chat composer so both warn/block at the
// same threshold.
export const SKILL_TURN_BUDGET_BYTES = 96 * 1024;

export interface SkillMarkdownFields {
  name: string;
  description: string;
  instructions: string;
}

/** Mirrors the Agent Skills SKILL.md shape: YAML frontmatter + Markdown body. */
export function buildSkillMarkdown({
  name,
  description,
  instructions,
}: SkillMarkdownFields): string {
  const frontmatterName = name.trim().length > 0 ? name.trim() : "skill-name";
  const frontmatterDescription = JSON.stringify(
    description.trim().length > 0
      ? description.trim()
      : "When to use this skill",
  );
  const body = instructions.trim().length > 0 ? instructions : "# Instructions";
  return `---\nname: ${frontmatterName}\ndescription: ${frontmatterDescription}\n---\n\n${body}\n`;
}

/** UTF-8 byte length, matching the backend's `SKILL.md` size check. */
export function utf8ByteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}
