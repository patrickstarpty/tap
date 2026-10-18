import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  AiAssetError,
  useCreateSkill,
  useReviseSkill,
} from "../../../../features/knowledge/api/aiAssets";
import { WORKSPACE_COPY } from "../copy";
import { SkillForm } from "./SkillForm";
import { SKILL_MD_BYTE_LIMIT } from "./skillMarkdown";

vi.mock(
  "../../../../features/knowledge/api/aiAssets",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/aiAssets")
      >();
    return {
      ...actual,
      useCreateSkill: vi.fn(),
      useReviseSkill: vi.fn(),
    };
  },
);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

function renderForm(
  overrides: {
    mode?: "create" | "edit" | "copy";
    skill?: Parameters<typeof SkillForm>[0]["skill"];
    createMutateAsync?: ReturnType<typeof vi.fn>;
    reviseMutateAsync?: ReturnType<typeof vi.fn>;
    onClose?: () => void;
  } = {},
) {
  const createMutateAsync = overrides.createMutateAsync ?? vi.fn();
  const reviseMutateAsync = overrides.reviseMutateAsync ?? vi.fn();
  vi.mocked(useCreateSkill).mockReturnValue({
    mutateAsync: createMutateAsync,
    isPending: false,
  } as never);
  vi.mocked(useReviseSkill).mockReturnValue({
    mutateAsync: reviseMutateAsync,
    isPending: false,
  } as never);

  const onClose = overrides.onClose ?? vi.fn();
  return {
    onClose,
    createMutateAsync,
    reviseMutateAsync,
    ...render(
      createElement(SkillForm, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        mode: overrides.mode ?? "create",
        skill: overrides.skill,
        onClose,
        opener: document.body,
      }),
      { wrapper },
    ),
  };
}

describe("SkillForm live preview and budget", () => {
  it("updates the SKILL.md preview as the fields change", async () => {
    const user = userEvent.setup();
    renderForm();
    await user.type(
      screen.getByRole("textbox", { name: "Name" }),
      "policy-lookup",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Description" }),
      "Look up policy terms",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Instructions" }),
      "Search the index.",
    );
    const preview = screen.getByText(/SKILL\.md preview/i).closest("div");
    expect(preview).not.toBeNull();
    expect(preview).toHaveTextContent("name: policy-lookup");
    expect(preview).toHaveTextContent("Search the index.");
  });

  it("flags content over the 256 KB budget and disables save", async () => {
    const user = userEvent.setup();
    renderForm();
    const instructions = screen.getByRole("textbox", { name: "Instructions" });
    const huge = "a".repeat(SKILL_MD_BYTE_LIMIT + 10);
    await user.click(instructions);
    await user.paste(huge);
    expect(
      screen.getByText("SKILL.md is over the 256 KB limit."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });
});

describe("SkillForm revision conflict", () => {
  it("shows a revision conflict message when the save returns 409 revision_conflict", async () => {
    const user = userEvent.setup();
    const reviseMutateAsync = vi.fn().mockRejectedValue(
      new AiAssetError({
        code: "revision_conflict",
        detail: "stale",
      }),
    );
    renderForm({
      mode: "edit",
      skill: {
        revisionId: "rev_1",
        skillId: "skill_1",
        assetId: "skill_1",
        displayName: "Policy lookup",
        contentDigest: "digest",
        applicableTasks: ["knowledge.answer"],
        sourceKind: "authored",
        builtIn: false,
        status: "enabled",
        isLatest: true,
        description: "Look up policy terms",
        instructions: "Search the index.",
      } as never,
      reviseMutateAsync,
    });

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText(
        "This content was updated elsewhere. Please refresh.",
      ),
    ).toBeVisible();
  });
});

describe("SkillForm create", () => {
  it("submits a new skill with the entered fields", async () => {
    const user = userEvent.setup();
    const createMutateAsync = vi
      .fn()
      .mockResolvedValue({ revisionId: "rev_new" });
    const onClose = vi.fn();
    renderForm({ createMutateAsync, onClose });

    await user.type(
      screen.getByRole("textbox", { name: "Name" }),
      "policy-lookup",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Description" }),
      "Look up policy terms",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Instructions" }),
      "Search the index.",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(createMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "policy-lookup",
        description: "Look up policy terms",
        instructions: "Search the index.",
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });
});
