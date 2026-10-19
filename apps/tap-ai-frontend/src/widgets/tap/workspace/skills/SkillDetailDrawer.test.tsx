import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  useSkill,
  useSkillRevisions,
} from "../../../../features/knowledge/api/aiAssets";
import { WORKSPACE_COPY } from "../copy";
import { SkillDetailDrawer } from "./SkillDetailDrawer";

vi.mock(
  "../../../../features/knowledge/api/aiAssets",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/aiAssets")
      >();
    return {
      ...actual,
      useSkill: vi.fn(),
      useSkillRevisions: vi.fn(),
    };
  },
);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

const BUILT_IN_DETAIL = {
  revisionId: "rev_1",
  skillId: "skill_1",
  assetId: "skill_1",
  displayName: "Policy lookup",
  contentDigest: "digest",
  applicableTasks: ["knowledge.answer"],
  sourceKind: "built_in",
  builtIn: true,
  status: "enabled",
  isLatest: true,
  description: "Looks up policy terms",
  instructions: "Search the index.",
  attachedReferences: null as string | null,
  files: [
    {
      relativePath: "SKILL.md",
      category: "skill",
      attached: true,
      sizeBytes: 10,
      sha256: "a",
    },
  ],
  frontmatter: { name: "policy-lookup" },
};

function renderDrawer(
  overrides: {
    detail?: typeof BUILT_IN_DETAIL;
    revisions?: Array<Record<string, unknown>>;
    onCopyAsCustom?: (detail: unknown) => void;
    onEdit?: (detail: unknown) => void;
  } = {},
) {
  vi.mocked(useSkill).mockReturnValue({
    data: overrides.detail ?? BUILT_IN_DETAIL,
    isPending: false,
    isError: false,
  } as never);
  vi.mocked(useSkillRevisions).mockReturnValue({
    data: overrides.revisions ?? [overrides.detail ?? BUILT_IN_DETAIL],
    isPending: false,
    isError: false,
  } as never);
  const onClose = vi.fn();
  const onUse = vi.fn();
  const onEdit = overrides.onEdit ?? vi.fn();
  const onCopyAsCustom = overrides.onCopyAsCustom ?? vi.fn();
  return {
    onClose,
    onUse,
    onEdit,
    onCopyAsCustom,
    ...render(
      createElement(SkillDetailDrawer, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        revisionId: "rev_1",
        onClose,
        onUse,
        onEdit,
        onCopyAsCustom,
        opener: document.body,
      }),
      { wrapper },
    ),
  };
}

describe("SkillDetailDrawer", () => {
  it("renders the four tabs for a Skill revision", () => {
    renderDrawer();
    expect(screen.getByRole("tab", { name: "Instructions" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Files" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Metadata" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Revision history" })).toBeVisible();
  });

  it("shows instructions content on the Instructions tab by default", () => {
    renderDrawer();
    expect(screen.getByText("Search the index.")).toBeVisible();
  });

  it("switches to the Files tab and lists attached files", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("tab", { name: "Files" }));
    expect(screen.getByText("SKILL.md")).toBeVisible();
  });

  it("offers copy-as-custom for a built-in skill and calls back with the detail", async () => {
    const user = userEvent.setup();
    const onCopyAsCustom = vi.fn();
    renderDrawer({ onCopyAsCustom });
    const button = screen.getByRole("button", { name: "Copy as custom" });
    await user.click(button);
    expect(onCopyAsCustom).toHaveBeenCalledWith(BUILT_IN_DETAIL);
  });

  it("does not offer edit for a built-in skill", () => {
    renderDrawer();
    expect(
      screen.queryByRole("button", { name: "Edit" }),
    ).not.toBeInTheDocument();
  });

  it("offers edit (not copy-as-custom) for a custom skill", () => {
    const customDetail = {
      ...BUILT_IN_DETAIL,
      sourceKind: "authored",
      builtIn: false,
    };
    renderDrawer({ detail: customDetail });
    expect(screen.getByRole("button", { name: "Edit" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Copy as custom" }),
    ).not.toBeInTheDocument();
  });

  it("does not offer Edit for an imported skill, and points at re-importing instead (I-3)", () => {
    const importedDetail = {
      ...BUILT_IN_DETAIL,
      sourceKind: "imported",
      builtIn: false,
    };
    renderDrawer({ detail: importedDetail });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy as custom" })).toBeNull();
    expect(
      screen.getByText(
        "Imported skills can't be edited in place — import the updated package as a new revision instead.",
      ),
    ).toBeVisible();
  });

  it("shows attached reference text and a fallback when none was attached (I-7)", async () => {
    const user = userEvent.setup();
    renderDrawer({
      detail: { ...BUILT_IN_DETAIL, attachedReferences: "Cited excerpt." },
    });
    expect(screen.getByText("Cited excerpt.")).toBeVisible();

    renderDrawer({ detail: { ...BUILT_IN_DETAIL, attachedReferences: null } });
    expect(
      screen.getAllByText("No reference text was attached.").length,
    ).toBeGreaterThan(0);
    await user.click;
  });

  it("labels each file by category, marks non-prompt files 'Saved, not executed', and links a download (I-7)", async () => {
    const user = userEvent.setup();
    renderDrawer({
      detail: {
        ...BUILT_IN_DETAIL,
        files: [
          {
            relativePath: "SKILL.md",
            category: "skill",
            attached: true,
            sizeBytes: 1,
            sha256: "a",
          },
          {
            relativePath: "scripts/run.py",
            category: "script",
            attached: false,
            sizeBytes: 1,
            sha256: "b",
          },
          {
            relativePath: "references/notes.md",
            category: "reference",
            attached: true,
            sizeBytes: 1,
            sha256: "c",
          },
        ],
      },
    });
    await user.click(screen.getByRole("tab", { name: "Files" }));

    expect(screen.getByText("Script · saved, not executed")).toBeVisible();
    expect(screen.getByText("Reference · attached")).toBeVisible();
    expect(screen.getByText("Saved, not executed")).toBeVisible();
    const downloadLinks = screen.getAllByRole("link", { name: "Download" });
    expect(downloadLinks.length).toBe(3);
    expect(downloadLinks[1]!.getAttribute("href")).toBe(
      "/api/v1/projects/project-1/ai/skills/rev_1/files/scripts/run.py",
    );
  });

  it("lets a revision history entry switch the drawer to that revision (I-7)", async () => {
    const user = userEvent.setup();
    const olderRevision = { ...BUILT_IN_DETAIL, revisionId: "rev_0" };
    renderDrawer({
      revisions: [olderRevision, BUILT_IN_DETAIL],
    });
    await user.click(screen.getByRole("tab", { name: "Revision history" }));

    const switchButton = screen.getByRole("button", { name: "rev_0" });
    await user.click(switchButton);

    expect(useSkill).toHaveBeenLastCalledWith("project-1", "rev_0");
  });

  it("hides Use in chat and Edit while viewing an older revision, since they must act on the viewed revision, not silently on the latest one (minor)", async () => {
    // A real `useSkill(projectId, activeRevisionId)` call re-fetches per
    // revision id -- unlike `renderDrawer`'s static mock, this needs a
    // dynamic one so switching revisions actually changes `detail`
    // (including `isLatest`).
    const user = userEvent.setup();
    const latest = {
      ...BUILT_IN_DETAIL,
      sourceKind: "authored",
      builtIn: false,
    };
    const older = { ...latest, revisionId: "rev_0", isLatest: false };
    vi.mocked(useSkill).mockImplementation(
      (_projectId: string, revisionId: string | null) =>
        ({
          data: revisionId === "rev_0" ? older : latest,
          isPending: false,
          isError: false,
        }) as never,
    );
    vi.mocked(useSkillRevisions).mockReturnValue({
      data: [older, latest],
      isPending: false,
      isError: false,
    } as never);
    render(
      createElement(SkillDetailDrawer, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        revisionId: "rev_1",
        onClose: vi.fn(),
        onUse: vi.fn(),
        onEdit: vi.fn(),
        onCopyAsCustom: vi.fn(),
        opener: document.body,
      }),
      { wrapper },
    );

    expect(screen.getByRole("button", { name: "Use in chat" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Edit" })).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "Revision history" }));
    await user.click(screen.getByRole("button", { name: "rev_0" }));

    expect(
      screen.queryByRole("button", { name: "Use in chat" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Edit" }),
    ).not.toBeInTheDocument();
  });

  it("shows a retry-able error instead of a blank drawer when the detail fails to load", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    vi.mocked(useSkill).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      refetch,
    } as never);
    vi.mocked(useSkillRevisions).mockReturnValue({
      data: [],
      isPending: false,
      isError: false,
    } as never);

    render(
      createElement(SkillDetailDrawer, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        revisionId: "rev_1",
        onClose: vi.fn(),
        onUse: vi.fn(),
        onEdit: vi.fn(),
        onCopyAsCustom: vi.fn(),
        opener: document.body,
      }),
      { wrapper },
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "This skill could not be loaded.",
    );
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });
});
