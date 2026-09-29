import { describe, expect, it } from "vitest";

import { isValidCatalogName, toSkillMarkdown } from "./skillMarkdown";

describe("skillMarkdown", () => {
  it("accepts kebab-case names only", () => {
    expect(isValidCatalogName("health-disclosure-check")).toBe(true);
    for (const bad of ["Health", "a--b", "-a", "a_b", "a b", ""]) {
      expect(isValidCatalogName(bad)).toBe(false);
    }
  });

  it("renders frontmatter and body", () => {
    expect(
      toSkillMarkdown({
        name: "bdd-writer",
        description: "Writes BDD",
        instructions: "Use Given/When/Then.",
      }),
    ).toBe(
      '---\nname: bdd-writer\ndescription: "Writes BDD"\n---\n\nUse Given/When/Then.\n',
    );
  });

  it("quotes and escapes a description with a colon and a newline", () => {
    expect(
      toSkillMarkdown({
        name: "bdd-writer",
        description: 'Writes BDD: uses "Given/When/Then"\nfor scenarios',
        instructions: "Use Given/When/Then.",
      }),
    ).toBe(
      '---\nname: bdd-writer\ndescription: "Writes BDD: uses \\"Given/When/Then\\"\\nfor scenarios"\n---\n\nUse Given/When/Then.\n',
    );
  });
});
