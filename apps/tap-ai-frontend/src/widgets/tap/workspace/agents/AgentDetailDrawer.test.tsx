import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AiAssetError,
  useAgent,
  useAgentRevisions,
  useReviseAgent,
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
  hasSkillUpdates: true,
  defaultModelAlias: null,
  skillRevisionIds: ["skill-rev-old"],
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

function mockFetchSkillDetail(options: { pending?: boolean } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      if (options.pending === true)
        return new Promise<Response>(() => {
          /* never resolves */
        });
      const match = /\/skills\/([^/]+)$/.exec(input);
      const revisionId = match?.[1] ?? "";
      return new Response(
        JSON.stringify({
          revisionId,
          skillId: "skill-1",
          assetId: "skill-1",
          displayName: "Citation skill (old)",
          description: "",
          instructions: "a",
          applicableTasks: ["knowledge.answer"],
          sourceKind: "authored",
          builtIn: false,
          status: "enabled",
          isLatest: false,
        }),
        { status: 200 },
      );
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderDrawer(
  overrides: {
    detail?: typeof DETAIL | undefined;
    detailError?: boolean;
    onEdit?: (detail: unknown) => void;
    onUse?: (revisionId: string) => void;
    onCopyAsCustom?: (detail: unknown) => void;
    onUpgraded?: (revisionId: string) => void;
    reviseMutate?: ReturnType<typeof vi.fn>;
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
  const reviseMutate = overrides.reviseMutate ?? vi.fn();
  vi.mocked(useReviseAgent).mockReturnValue({
    mutate: reviseMutate,
    isPending: false,
  } as never);
  const onEdit = overrides.onEdit ?? vi.fn();
  const onUse = overrides.onUse ?? vi.fn();
  const onCopyAsCustom = overrides.onCopyAsCustom ?? vi.fn();
  const onUpgraded = overrides.onUpgraded ?? vi.fn();
  return {
    onEdit,
    onUse,
    onCopyAsCustom,
    onUpgraded,
    reviseMutate,
    ...render(
      createElement(AgentDetailDrawer, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        revisionId: "rev_1",
        skills: [LATEST_SKILL],
        onClose: vi.fn(),
        onUse,
        onEdit,
        onCopyAsCustom,
        onUpgraded,
        opener: document.body,
      }),
      { wrapper },
    ),
  };
}

describe("AgentDetailDrawer", () => {
  it("shows the overview tab with default model and revision", () => {
    mockFetchSkillDetail();
    renderDrawer();
    expect(
      screen.getByText("Helps review underwriting evidence."),
    ).toBeVisible();
    expect(screen.getByText("Use the global default model")).toBeVisible();
    expect(screen.getByText("rev_1")).toBeVisible();
  });

  it("switches to the Skills tab and lists bundled skills", async () => {
    mockFetchSkillDetail();
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("tab", { name: "Skills" }));
    expect(await screen.findByText("Citation skill (old)")).toBeVisible();
  });

  it("shows an error and a retry button when the Agent detail fails to load", () => {
    mockFetchSkillDetail();
    renderDrawer({ detailError: true });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Agents could not be loaded.",
    );
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("shows Copy as custom instead of Edit for a built-in agent", () => {
    mockFetchSkillDetail();
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

  it("disables Upgrade until every bundled Skill's latest revision resolves", () => {
    mockFetchSkillDetail({ pending: true });
    renderDrawer();
    expect(screen.getByRole("button", { name: "Upgrade" })).toBeDisabled();
  });

  it("revises the agent directly with the latest skill revision on Upgrade", async () => {
    mockFetchSkillDetail();
    const user = userEvent.setup();
    const reviseMutate = vi.fn();
    renderDrawer({ reviseMutate });

    expect(
      screen.getByText("A bundled skill has a newer revision."),
    ).toBeVisible();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Upgrade" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Upgrade" }));

    expect(reviseMutate.mock.calls[0]![0]).toEqual({
      agentId: "agent_1",
      request: expect.objectContaining({
        baseRevisionId: "rev_1",
        systemInstruction: "Review the evidence before escalating.",
        skillRevisionIds: ["skill-rev-new"],
      }),
    });
  });

  it("never offers Upgrade for a built-in agent, even with a bundled Skill update", () => {
    mockFetchSkillDetail();
    renderDrawer({ detail: BUILT_IN_DETAIL });
    expect(
      screen.getByText("A bundled skill has a newer revision."),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Upgrade" }),
    ).not.toBeInTheDocument();
  });

  it("disables Upgrade when the revision has no systemInstruction to resubmit", () => {
    mockFetchSkillDetail();
    renderDrawer({ detail: { ...DETAIL, systemInstruction: null } });
    expect(screen.getByRole("button", { name: "Upgrade" })).toBeDisabled();
  });

  it("moves the drawer to the new revision when Upgrade succeeds", async () => {
    mockFetchSkillDetail();
    const user = userEvent.setup();
    const reviseMutate = vi.fn();
    const onUpgraded = vi.fn();
    renderDrawer({ reviseMutate, onUpgraded });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Upgrade" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Upgrade" }));

    const options = reviseMutate.mock.calls[0]![1] as {
      onSuccess: (result: { revisionId: string }) => void;
    };
    options.onSuccess({ revisionId: "rev_2" });
    expect(onUpgraded).toHaveBeenCalledWith("rev_2");
  });

  it("shows a revision-conflict message when Upgrade returns 409 revision_conflict", async () => {
    mockFetchSkillDetail();
    const user = userEvent.setup();
    const reviseMutate = vi.fn();
    renderDrawer({ reviseMutate });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Upgrade" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Upgrade" }));

    const options = reviseMutate.mock.calls[0]![1] as {
      onError: (cause: unknown) => void;
    };
    options.onError(
      new AiAssetError({ code: "revision_conflict", detail: "stale" }),
    );

    expect(
      await screen.findByText(
        "This content was updated elsewhere. Please refresh.",
      ),
    ).toBeVisible();
  });

  it("shows a generic error message on other Upgrade failures", async () => {
    mockFetchSkillDetail();
    const user = userEvent.setup();
    const reviseMutate = vi.fn();
    renderDrawer({ reviseMutate });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Upgrade" })).toBeEnabled(),
    );
    await user.click(screen.getByRole("button", { name: "Upgrade" }));

    const options = reviseMutate.mock.calls[0]![1] as {
      onError: (cause: unknown) => void;
    };
    options.onError(new Error("network down"));

    expect(
      await screen.findByText("This agent could not be upgraded."),
    ).toBeVisible();
  });
});
