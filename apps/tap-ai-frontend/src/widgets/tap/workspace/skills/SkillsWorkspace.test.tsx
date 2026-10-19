import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  useSetSkillEnabled,
  useSkill,
  useSkillRevisions,
  useSkills,
} from "../../../../features/knowledge/api/aiAssets";
import { WORKSPACE_COPY } from "../copy";
import { SkillsWorkspace } from "./SkillsWorkspace";

vi.mock(
  "../../../../features/knowledge/api/aiAssets",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/aiAssets")
      >();
    return {
      ...actual,
      useSkills: vi.fn(),
      useSkill: vi.fn(),
      useSkillRevisions: vi.fn(),
      useSetSkillEnabled: vi.fn(),
      useStageSkillImport: vi.fn(() => ({
        mutateAsync: vi.fn(),
        isPending: false,
      })),
      useCommitSkillImport: vi.fn(() => ({
        mutateAsync: vi.fn(),
        isPending: false,
      })),
      useCreateSkill: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
      useReviseSkill: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
    };
  },
);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

const SKILLS = [
  {
    revisionId: "rev_builtin",
    skillId: "skill_builtin",
    assetId: "skill_builtin",
    displayName: "Built-in reviewer",
    contentDigest: "d1",
    applicableTasks: ["knowledge.answer"],
    sourceKind: "built_in",
    builtIn: true,
    status: "enabled",
    isLatest: true,
  },
  {
    revisionId: "rev_custom",
    skillId: "skill_custom",
    assetId: "skill_custom",
    displayName: "Custom policy helper",
    contentDigest: "d2",
    applicableTasks: ["knowledge.answer"],
    sourceKind: "authored",
    builtIn: false,
    status: "enabled",
    isLatest: true,
  },
  {
    revisionId: "rev_disabled",
    skillId: "skill_disabled",
    assetId: "skill_disabled",
    displayName: "Retired helper",
    contentDigest: "d3",
    applicableTasks: ["knowledge.answer"],
    sourceKind: "authored",
    builtIn: false,
    status: "disabled",
    isLatest: true,
  },
];

function renderWorkspace(
  overrides: {
    skills?: typeof SKILLS;
    locale?: "en" | "zh";
    onUse?: (revisionId: string) => void;
    setEnabledMutate?: ReturnType<typeof vi.fn>;
  } = {},
) {
  vi.mocked(useSkills).mockReturnValue({
    data: overrides.skills ?? SKILLS,
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  } as never);
  vi.mocked(useSkill).mockReturnValue({
    data: undefined,
    isPending: true,
    isError: false,
  } as never);
  vi.mocked(useSkillRevisions).mockReturnValue({
    data: undefined,
    isPending: true,
    isError: false,
  } as never);
  const setEnabledMutate = overrides.setEnabledMutate ?? vi.fn();
  vi.mocked(useSetSkillEnabled).mockReturnValue({
    mutate: setEnabledMutate,
    isPending: false,
  } as never);
  const onUse = overrides.onUse ?? vi.fn();
  const copy = WORKSPACE_COPY[overrides.locale ?? "en"];
  return {
    onUse,
    setEnabledMutate,
    ...render(
      createElement(SkillsWorkspace, {
        projectId: "project-1",
        copy,
        onUse,
      }),
      { wrapper },
    ),
  };
}

describe("SkillsWorkspace filters", () => {
  it("shows all latest skills by default", () => {
    renderWorkspace();
    expect(screen.getByText("Built-in reviewer")).toBeVisible();
    expect(screen.getByText("Custom policy helper")).toBeVisible();
    expect(screen.getByText("Retired helper")).toBeVisible();
  });

  it("filters to only built-in skills", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter" }),
      "Built-in",
    );
    expect(screen.getByText("Built-in reviewer")).toBeVisible();
    expect(screen.queryByText("Custom policy helper")).not.toBeInTheDocument();
    expect(screen.queryByText("Retired helper")).not.toBeInTheDocument();
  });

  it("filters to only custom skills", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter" }),
      "Custom",
    );
    expect(screen.getByText("Custom policy helper")).toBeVisible();
    expect(screen.queryByText("Built-in reviewer")).not.toBeInTheDocument();
    expect(screen.queryByText("Retired helper")).not.toBeInTheDocument();
  });

  it("filters to only disabled skills", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter" }),
      "Disabled",
    );
    expect(screen.getByText("Retired helper")).toBeVisible();
    expect(screen.queryByText("Built-in reviewer")).not.toBeInTheDocument();
    expect(screen.queryByText("Custom policy helper")).not.toBeInTheDocument();
  });

  it("shows a no-results message when a filter matches nothing", async () => {
    const user = userEvent.setup();
    renderWorkspace({ skills: [] });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter" }),
      "Disabled",
    );
    expect(screen.getByText("No skills match this filter.")).toBeVisible();
  });
});

describe("SkillsWorkspace actions", () => {
  it("calls onUse with the revision id when Use in chat is clicked", async () => {
    const user = userEvent.setup();
    const onUse = vi.fn();
    renderWorkspace({ onUse });
    const row = screen.getByText("Custom policy helper").closest("li")!;
    await user.click(
      screen
        .getAllByRole("button", { name: "Use in chat" })
        .find((button) => row.contains(button))!,
    );
    expect(onUse).toHaveBeenCalledWith("rev_custom");
  });

  it("opens the import dialog from the Import skill button", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(screen.getByRole("button", { name: "Import skill" }));
    expect(screen.getByText("Import a skill")).toBeVisible();
  });

  it("opens the create form from the New skill button", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(screen.getByRole("button", { name: "New skill" }));
    expect(screen.getByText("New skill", { selector: "h2" })).toBeVisible();
  });
});

describe("SkillsWorkspace locale", () => {
  it("renders Chinese copy", () => {
    renderWorkspace({ locale: "zh" });
    expect(screen.getByText("技能")).toBeVisible();
    expect(screen.getByRole("button", { name: "导入 Skill" })).toBeVisible();
    expect(screen.getByRole("button", { name: "新建 Skill" })).toBeVisible();
  });
});
