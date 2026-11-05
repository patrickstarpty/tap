import { describe, expect, it } from "vitest";

import {
  SKILL_MD_BYTE_LIMIT,
  buildSkillMarkdown,
  utf8ByteLength,
} from "./skillMarkdown";

describe("buildSkillMarkdown", () => {
  it("renders YAML frontmatter followed by the instructions body", () => {
    const markdown = buildSkillMarkdown({
      name: "policy-lookup",
      description: "Look up policy terms",
      instructions: "# Steps\n1. Search the policy index.",
    });
    expect(markdown).toBe(
      '---\nname: policy-lookup\ndescription: "Look up policy terms"\n---\n\n# Steps\n1. Search the policy index.\n',
    );
  });

  it("falls back to placeholders for empty fields so the preview never renders blank", () => {
    const markdown = buildSkillMarkdown({
      name: "",
      description: "",
      instructions: "",
    });
    expect(markdown).toContain("name: skill-name");
    expect(markdown).toContain("When to use this skill");
    expect(markdown).toContain("# Instructions");
  });
});

describe("utf8ByteLength", () => {
  it("counts UTF-8 bytes, not JS string length, for multi-byte characters", () => {
    // Chinese characters are typically 3 bytes in UTF-8.
    expect(utf8ByteLength("中文")).toBe(6);
    expect("中文".length).toBe(2);
  });

  it("flags content over the 256 KB SKILL.md limit", () => {
    const small = "a".repeat(10);
    const huge = "a".repeat(SKILL_MD_BYTE_LIMIT + 1);
    expect(utf8ByteLength(small)).toBeLessThan(SKILL_MD_BYTE_LIMIT);
    expect(utf8ByteLength(huge)).toBeGreaterThan(SKILL_MD_BYTE_LIMIT);
  });
});
