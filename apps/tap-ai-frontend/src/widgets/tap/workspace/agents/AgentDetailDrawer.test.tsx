import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
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

function mockFetchSkillDetail() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
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
    onEdit?: (detail: unknown) => void;
    onUse?: (revisionId: string) => void;
  } = {},
) {
  vi.mocked(useAgent).mockReturnValue({
    data: DETAIL,
    isPending: false,
    isError: false,
  } as never);
  vi.mocked(useAgentRevisions).mockReturnValue({
    data: [{ revisionId: "rev_1" }],
    isPending: false,
    isError: false,
  } as never);
  const onEdit = overrides.onEdit ?? vi.fn();
  const onUse = overrides.onUse ?? vi.fn();
  return {
    onEdit,
    onUse,
    ...render(
      createElement(AgentDetailDrawer, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        revisionId: "rev_1",
        skills: [LATEST_SKILL],
        onClose: vi.fn(),
        onUse,
        onEdit,
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

  it("shows the hasSkillUpdates notice and opens edit with the latest skill revision on Upgrade", async () => {
    mockFetchSkillDetail();
    const user = userEvent.setup();
    const onEdit = vi.fn();
    renderDrawer({ onEdit });

    expect(
      screen.getByText("A bundled skill has a newer revision."),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Upgrade" }));

    await waitFor(() =>
      expect(onEdit).toHaveBeenCalledWith(
        expect.objectContaining({
          revisionId: "rev_1",
          skillRevisionIds: ["skill-rev-new"],
        }),
      ),
    );
  });
});
