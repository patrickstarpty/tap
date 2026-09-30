import { expect, it } from "vitest";
import { catalogMarkdown } from "./catalogMarkdown";

it("exports a coding-agent Markdown definition with YAML frontmatter", () => {
  expect(
    catalogMarkdown("agent", {
      name: "claims-reviewer",
      description: "Review claim controls when approval behavior changes.",
      instructions: "# Claims reviewer\n\nCheck evidence before approval.",
    }),
  ).toEqual({
    filename: "claims-reviewer.md",
    content:
      '---\nname: claims-reviewer\ndescription: "Review claim controls when approval behavior changes."\n---\n\n# Claims reviewer\n\nCheck evidence before approval.\n',
  });
});

it("exports an Agent Skills SKILL.md and rejects invalid names", () => {
  expect(
    catalogMarkdown("skill", {
      name: "citation-check",
      description: "Check citations when answers contain document claims.",
      instructions: "# Citation check\n\nVerify each cited paragraph.",
    }).filename,
  ).toBe("SKILL.md");
  expect(() =>
    catalogMarkdown("skill", {
      name: "Citation Check",
      description: "Check citations.",
      instructions: "Verify claims.",
    }),
  ).toThrow();
});
