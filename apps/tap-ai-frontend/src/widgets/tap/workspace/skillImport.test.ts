import { expect, it } from "vitest";
import { previewSkillMarkdown } from "./skillImport";
const prefix = "---\nname: claims-reviewer\ndescription: Review claims\n";
it("previews YAML tools and preserves inert instructions", () => {
  expect(
    previewSkillMarkdown(
      prefix +
        "allowed-tools: knowledge.search, graph.neighbors\n---\n<script>alert(1)</script>\n```sh\nrm -rf /\n```",
    ),
  ).toEqual({
    name: "claims-reviewer",
    description: "Review claims",
    requestedTools: ["knowledge.search", "graph.neighbors"],
    instructions: "<script>alert(1)</script>\n```sh\nrm -rf /\n```",
  });
});
it.each([
  "metadata: &x {nested: 1}\nextra: *x",
  "metadata: !!js/function 'alert(1)'",
  "name: another-name",
  "metadata: {nested: .inf}",
])("rejects unsafe or ambiguous front matter: %s", (yaml) => {
  expect(() =>
    previewSkillMarkdown(prefix + yaml + "\n---\nCheck evidence."),
  ).toThrow();
});
