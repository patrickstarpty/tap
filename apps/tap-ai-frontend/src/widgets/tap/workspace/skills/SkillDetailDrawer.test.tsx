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
});
