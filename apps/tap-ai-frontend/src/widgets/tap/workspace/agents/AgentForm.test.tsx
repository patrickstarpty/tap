import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AiAssetError,
  useCreateAgent,
  useReviseAgent,
  type SkillRevision,
  type SkillRevisionDetail,
} from "../../../../features/knowledge/api/aiAssets";
import { WORKSPACE_COPY } from "../copy";
import { AgentForm, AGENT_SKILLS_BUDGET_BYTES } from "./AgentForm";

vi.mock(
  "../../../../features/knowledge/api/aiAssets",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/aiAssets")
      >();
    return {
      ...actual,
      useCreateAgent: vi.fn(),
      useReviseAgent: vi.fn(),
    };
  },
);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

const APPLICABLE_SKILL: SkillRevision = {
  revisionId: "skill-rev-1",
  skillId: "skill-1",
  assetId: "skill-1",
  displayName: "Citation skill",
  contentDigest: "digest-1",
  applicableTasks: ["knowledge.answer"],
  sourceKind: "authored",
  builtIn: false,
  status: "enabled",
  isLatest: true,
} as SkillRevision;

const NOT_APPLICABLE_SKILL: SkillRevision = {
  revisionId: "skill-rev-2",
  skillId: "skill-2",
  assetId: "skill-2",
  displayName: "Test plan skill",
  contentDigest: "digest-2",
  applicableTasks: ["test-plan.generate"],
  sourceKind: "authored",
  builtIn: false,
  status: "enabled",
  isLatest: true,
} as SkillRevision;

function skillDetailFor(
  revisionId: string,
  instructionsLength: number,
): SkillRevisionDetail {
  return {
    revisionId,
    skillId: revisionId,
    assetId: revisionId,
    displayName: revisionId,
    description: "",
    instructions: "a".repeat(instructionsLength),
    applicableTasks: ["knowledge.answer"],
    sourceKind: "authored",
    builtIn: false,
    status: "enabled",
    isLatest: true,
    contentDigest: "digest",
    files: [],
  } as unknown as SkillRevisionDetail;
}

function mockFetchSkillDetail(bySize: Record<string, number>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const match = /\/skills\/([^/]+)$/.exec(input);
      const revisionId = match?.[1] ?? "";
      const size = bySize[revisionId] ?? 0;
      return new Response(JSON.stringify(skillDetailFor(revisionId, size)), {
        status: 200,
      });
    }),
  );
}

function renderForm(
  overrides: {
    mode?: "create" | "edit";
    agent?: Parameters<typeof AgentForm>[0]["agent"];
    skills?: readonly SkillRevision[];
    createMutateAsync?: ReturnType<typeof vi.fn>;
    reviseMutateAsync?: ReturnType<typeof vi.fn>;
    onClose?: () => void;
  } = {},
) {
  const createMutateAsync = overrides.createMutateAsync ?? vi.fn();
  const reviseMutateAsync = overrides.reviseMutateAsync ?? vi.fn();
  vi.mocked(useCreateAgent).mockReturnValue({
    mutateAsync: createMutateAsync,
    isPending: false,
  } as never);
  vi.mocked(useReviseAgent).mockReturnValue({
    mutateAsync: reviseMutateAsync,
    isPending: false,
  } as never);

  const onClose = overrides.onClose ?? vi.fn();
  return {
    onClose,
    createMutateAsync,
    reviseMutateAsync,
    ...render(
      createElement(AgentForm, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        mode: overrides.mode ?? "create",
        agent: overrides.agent,
        skills: overrides.skills ?? [],
        models: [],
        onClose,
        opener: document.body,
      }),
      { wrapper },
    ),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AgentForm fields and tools", () => {
  it("shows the fixed read-only built-in tools text", () => {
    mockFetchSkillDetail({});
    renderForm();
    expect(
      screen.getByText("Knowledge search, Knowledge answer (always enabled)"),
    ).toBeVisible();
  });

  it("grays out a Skill that isn't applicable to knowledge answers", () => {
    mockFetchSkillDetail({});
    renderForm({ skills: [APPLICABLE_SKILL, NOT_APPLICABLE_SKILL] });
    expect(
      screen.getByRole("checkbox", { name: "Citation skill" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("checkbox", { name: "Test plan skill" }),
    ).toBeDisabled();
  });

  it("gates Save on name, description and system instruction", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    renderForm();
    const save = screen.getByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "a");
    await user.type(screen.getByRole("textbox", { name: "Description" }), "d");
    await user.type(
      screen.getByRole("textbox", { name: "System instruction" }),
      "s",
    );
    expect(save).toBeEnabled();
  });
});

describe("AgentForm Skill budget", () => {
  it("warns at 90% of the per-turn Skill budget without blocking save", async () => {
    const user = userEvent.setup();
    const nearBudgetSize = Math.ceil(AGENT_SKILLS_BUDGET_BYTES * 0.95);
    mockFetchSkillDetail({ "skill-rev-1": nearBudgetSize });
    renderForm({ skills: [APPLICABLE_SKILL] });

    await user.click(screen.getByRole("checkbox", { name: "Citation skill" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "a");
    await user.type(screen.getByRole("textbox", { name: "Description" }), "d");
    await user.type(
      screen.getByRole("textbox", { name: "System instruction" }),
      "s",
    );

    expect(
      await screen.findByText(
        "Selected skills are near the per-turn budget. Remove some before they are needed together.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("blocks save and lists the largest skills when over the per-turn budget", async () => {
    const user = userEvent.setup();
    const overBudgetSize = AGENT_SKILLS_BUDGET_BYTES + 100;
    mockFetchSkillDetail({ "skill-rev-1": overBudgetSize });
    renderForm({ skills: [APPLICABLE_SKILL] });

    await user.click(screen.getByRole("checkbox", { name: "Citation skill" }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "a");
    await user.type(screen.getByRole("textbox", { name: "Description" }), "d");
    await user.type(
      screen.getByRole("textbox", { name: "System instruction" }),
      "s",
    );

    expect(
      await screen.findByText(
        "Selected skills exceed the per-turn budget. Remove some before saving.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Largest skills: skill-rev-1")).toBeVisible();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled(),
    );
  });
});

describe("AgentForm save", () => {
  it("submits a new agent with the entered fields", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    const createMutateAsync = vi
      .fn()
      .mockResolvedValue({ revisionId: "rev_new" });
    const onClose = vi.fn();
    renderForm({ createMutateAsync, onClose });

    await user.type(
      screen.getByRole("textbox", { name: "Name" }),
      "underwriting-helper",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Description" }),
      "Helps with underwriting.",
    );
    await user.type(
      screen.getByRole("textbox", { name: "System instruction" }),
      "Review the evidence.",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(createMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "underwriting-helper",
        description: "Helps with underwriting.",
        systemInstruction: "Review the evidence.",
      }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("shows a name-conflict message when the save returns 409 agent_name_conflict", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    const createMutateAsync = vi
      .fn()
      .mockRejectedValue(
        new AiAssetError({ code: "agent_name_conflict", detail: "taken" }),
      );
    renderForm({ createMutateAsync });

    await user.type(screen.getByRole("textbox", { name: "Name" }), "dup");
    await user.type(screen.getByRole("textbox", { name: "Description" }), "d");
    await user.type(
      screen.getByRole("textbox", { name: "System instruction" }),
      "s",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(
      await screen.findByText("An agent with this name already exists."),
    ).toBeVisible();
  });
});
