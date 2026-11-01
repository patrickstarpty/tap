import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  useSkill,
  useSkillRevisions,
} from "../../../../features/knowledge/api/aiAssets";
import { WORKSPACE_COPY } from "../copy";
import { RevisionCompare } from "./RevisionCompare";

vi.mock(
  "../../../../features/knowledge/api/aiAssets",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/aiAssets")
      >();
    return { ...actual, useSkill: vi.fn(), useSkillRevisions: vi.fn() };
  },
);

function revision(revisionId: string, versionLabel: string, body: string) {
  return {
    revisionId,
    skillId: "skill_1",
    assetId: "skill_1",
    displayName: "Policy lookup",
    contentDigest: revisionId,
    applicableTasks: ["knowledge.answer"],
    sourceKind: "authored",
    builtIn: false,
    status: "enabled",
    isLatest: revisionId === "rev_2",
    versionLabel,
    description: "Looks up policy terms",
    instructions: body,
    attachedReferences: null,
    files: [],
    frontmatter: { name: "policy-lookup" },
  };
}

const REVISIONS = [
  revision("rev_1", "r1", "Search the old index."),
  revision("rev_2", "r2", "Search the new index first."),
];

function renderCompare() {
  vi.mocked(useSkillRevisions).mockReturnValue({
    data: REVISIONS,
    isPending: false,
    isError: false,
  } as never);
  vi.mocked(useSkill).mockImplementation(
    (_projectId: string, revisionId: string | null) =>
      ({
        data: REVISIONS.find((item) => item.revisionId === revisionId),
        isPending: false,
        isError: false,
      }) as never,
  );
  render(
    createElement(RevisionCompare, {
      projectId: "project-1",
      skillId: "skill_1",
      copy: WORKSPACE_COPY.en,
    }),
  );
}

describe("RevisionCompare", () => {
  it("renders the two chosen revisions side by side", async () => {
    const user = userEvent.setup();
    renderCompare();
    expect(useSkillRevisions).toHaveBeenCalledWith("project-1", "skill_1");
    expect(screen.getByText("Choose two revisions to compare.")).toBeVisible();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Left revision" }),
      "rev_1",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Right revision" }),
      "rev_2",
    );

    const left = screen.getByRole("region", { name: "Left revision: r1" });
    const right = screen.getByRole("region", { name: "Right revision: r2" });
    expect(within(left).getByText(/Search the old index\./)).toBeVisible();
    expect(within(left).getByText(/name: policy-lookup/)).toBeVisible();
    expect(
      within(right).getByText(/Search the new index first\./),
    ).toBeVisible();
    expect(within(right).queryByText(/Search the old index\./)).toBeNull();
    expect(useSkill).toHaveBeenCalledWith("project-1", "rev_1");
    expect(useSkill).toHaveBeenCalledWith("project-1", "rev_2");
  });
});
