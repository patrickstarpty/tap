import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useAgent,
  useAgentRevisions,
  type SkillRevision,
} from "../../../../features/knowledge/api/aiAssets";
import { WORKSPACE_COPY } from "../copy";
import { AgentDetailDrawer } from "./AgentDetailDrawer";

vi.mock(
  "../../../../features/knowledge/api/aiAssets",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/aiAssets")
      >();
    return {
      ...actual,
      useAgent: vi.fn(),
      useAgentRevisions: vi.fn(),
    };
  },
);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

const DETAIL = {
  revisionId: "rev_1",
  agentId: "agent_1",
  assetId: "agent_1",
  displayName: "Underwriting helper",
  description: "Helps review underwriting evidence.",
  systemInstruction: "Review the evidence before escalating." as string | null,
  contentDigest: "digest",
  outputSchemaDigest: "schema-digest",
  toolAllowlist: ["knowledge.search", "knowledge.answer"],
  sourceKind: "authored",
  builtIn: false,
  status: "enabled",
  isLatest: true,
  // Deprecated: the server always sends `false` now; even a stale `true`
  // must not bring the old upgrade prompt back.
  hasSkillUpdates: true,
  defaultModelAlias: null,
  pinnedSkillIds: ["skill-1"],
  excludedSkillIds: ["skill-2"],
};

const BUILT_IN_DETAIL = { ...DETAIL, sourceKind: "built_in", builtIn: true };

const LATEST_SKILL: SkillRevision = {
  revisionId: "skill-rev-new",
  skillId: "skill-1",
  assetId: "skill-1",
  displayName: "Citation skill",
  contentDigest: "digest",
  applicableTasks: ["knowledge.answer"],
  sourceKind: "authored",
  builtIn: false,
  status: "enabled",
  isLatest: true,
} as SkillRevision;

const EXCLUDED_SKILL: SkillRevision = {
  ...LATEST_SKILL,
  revisionId: "skill-rev-2",
  skillId: "skill-2",
  assetId: "skill-2",
  displayName: "Glossary skill",
} as SkillRevision;

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderDrawer(
  overrides: {
    detail?: typeof DETAIL | undefined;
    detailError?: boolean;
    skills?: readonly SkillRevision[];
    onEdit?: (detail: unknown) => void;
    onUse?: (revisionId: string) => void;
    onCopyAsCustom?: (detail: unknown) => void;
  } = {},
) {
  vi.mocked(useAgent).mockReturnValue({
    data:
      overrides.detailError === true ? undefined : (overrides.detail ?? DETAIL),
    isPending: false,
    isError: overrides.detailError === true,
    refetch: vi.fn(),
  } as never);
  vi.mocked(useAgentRevisions).mockReturnValue({
    data: [{ revisionId: "rev_1" }],
    isPending: false,
    isError: false,
  } as never);
  const onEdit = overrides.onEdit ?? vi.fn();
  const onUse = overrides.onUse ?? vi.fn();
  const onCopyAsCustom = overrides.onCopyAsCustom ?? vi.fn();
  return {
    onEdit,
    onUse,
    onCopyAsCustom,
    ...render(
      createElement(AgentDetailDrawer, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        revisionId: "rev_1",
        skills: overrides.skills ?? [LATEST_SKILL, EXCLUDED_SKILL],
        onClose: vi.fn(),
        onUse,
        onEdit,
        onCopyAsCustom,
        opener: document.body,
      }),
      { wrapper },
    ),
  };
}

describe("AgentDetailDrawer", () => {
  it("shows the overview tab with default model and revision", () => {
    renderDrawer();
    expect(
      screen.getByText("Helps review underwriting evidence."),
    ).toBeVisible();
    expect(screen.getByText("Use the global default model")).toBeVisible();
    expect(screen.getByText("rev_1")).toBeVisible();
  });

  it("switches to the Skills tab and lists pinned and excluded skills", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("tab", { name: "Skills" }));
    const pinned = screen.getByRole("list", { name: "Pinned Skills" });
    const excluded = screen.getByRole("list", { name: "Excluded Skills" });
    expect(within(pinned).getByText("Citation skill")).toBeVisible();
    expect(within(excluded).getByText("Glossary skill")).toBeVisible();
  });

  it("shows empty pinned and excluded lists", async () => {
    const user = userEvent.setup();
    renderDrawer({
      detail: { ...DETAIL, pinnedSkillIds: [], excludedSkillIds: [] },
    });
    await user.click(screen.getByRole("tab", { name: "Skills" }));
    expect(screen.getByText("No pinned skills.")).toBeVisible();
    expect(screen.getByText("No excluded skills.")).toBeVisible();
  });

  it("shows an error and a retry button when the Agent detail fails to load", () => {
    renderDrawer({ detailError: true });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Agents could not be loaded.",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("shows Copy as custom instead of Edit for a built-in agent", () => {
    const onCopyAsCustom = vi.fn();
    renderDrawer({ detail: BUILT_IN_DETAIL, onCopyAsCustom });
    expect(
      screen.queryByRole("button", { name: "Edit" }),
    ).not.toBeInTheDocument();
    screen.getByRole("button", { name: "Copy as custom" }).click();
    expect(onCopyAsCustom).toHaveBeenCalledWith(
      expect.objectContaining({ revisionId: "rev_1" }),
    );
  });

  it("shows no upgrade prompt", () => {
    renderDrawer();
    expect(
      screen.queryByRole("button", { name: "Upgrade" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/newer revision/)).not.toBeInTheDocument();
  });

  it("warns when a pinned skill has been disabled (I-6)", async () => {
    renderDrawer({
      skills: [{ ...LATEST_SKILL, status: "disabled" } as SkillRevision],
    });

    expect(
      screen.getByText(
        "A pinned skill has been disabled and will be skipped when this agent answers.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Citation skill")).toBeVisible();

    await userEvent.setup().click(screen.getByRole("tab", { name: "Skills" }));
    expect(screen.getByText("Citation skill (Disabled)")).toBeVisible();
  });
});
