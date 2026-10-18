import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  useAgent,
  useAgentRevisions,
  useAgents,
  useSetAgentEnabled,
  useSkills,
} from "../../../../features/knowledge/api/aiAssets";
import { useModelCatalog } from "../../../../features/knowledge/api/modelCatalog";
import { WORKSPACE_COPY } from "../copy";
import { AgentsWorkspace } from "./AgentsWorkspace";

vi.mock(
  "../../../../features/knowledge/api/aiAssets",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/aiAssets")
      >();
    return {
      ...actual,
      useAgents: vi.fn(),
      useAgent: vi.fn(),
      useAgentRevisions: vi.fn(),
      useSetAgentEnabled: vi.fn(),
      useSkills: vi.fn(),
      useCreateAgent: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
      useReviseAgent: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
    };
  },
);

vi.mock(
  "../../../../features/knowledge/api/modelCatalog",
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import("../../../../features/knowledge/api/modelCatalog")
      >();
    return {
      ...actual,
      useModelCatalog: vi.fn(),
    };
  },
);

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

const AGENTS = [
  {
    revisionId: "rev_builtin",
    agentId: "agent_builtin",
    assetId: "agent_builtin",
    displayName: "Built-in reviewer",
    description: "Reviews things.",
    contentDigest: "d1",
    outputSchemaDigest: "o1",
    toolAllowlist: ["knowledge.search", "knowledge.answer"],
    sourceKind: "built_in",
    builtIn: true,
    status: "enabled",
    isLatest: true,
    hasSkillUpdates: false,
    defaultModelAlias: null,
    skillRevisionIds: [],
  },
  {
    revisionId: "rev_custom",
    agentId: "agent_custom",
    assetId: "agent_custom",
    displayName: "Custom helper",
    description: "Helps with a custom workflow.",
    contentDigest: "d2",
    outputSchemaDigest: "o2",
    toolAllowlist: ["knowledge.search", "knowledge.answer"],
    sourceKind: "authored",
    builtIn: false,
    status: "enabled",
    isLatest: true,
    hasSkillUpdates: true,
    defaultModelAlias: null,
    skillRevisionIds: [],
  },
  {
    revisionId: "rev_disabled",
    agentId: "agent_disabled",
    assetId: "agent_disabled",
    displayName: "Retired helper",
    description: "No longer used.",
    contentDigest: "d3",
    outputSchemaDigest: "o3",
    toolAllowlist: ["knowledge.search", "knowledge.answer"],
    sourceKind: "authored",
    builtIn: false,
    status: "disabled",
    isLatest: true,
    hasSkillUpdates: false,
    defaultModelAlias: null,
    skillRevisionIds: [],
  },
];

function renderWorkspace(
  overrides: {
    agents?: typeof AGENTS;
    locale?: "en" | "zh";
    onUse?: (revisionId: string) => void;
    setEnabledMutate?: ReturnType<typeof vi.fn>;
  } = {},
) {
  vi.mocked(useAgents).mockReturnValue({
    data: overrides.agents ?? AGENTS,
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  } as never);
  vi.mocked(useSkills).mockReturnValue({
    data: [],
    isPending: false,
    isError: false,
  } as never);
  vi.mocked(useAgent).mockReturnValue({
    data: undefined,
    isPending: true,
    isError: false,
  } as never);
  vi.mocked(useAgentRevisions).mockReturnValue({
    data: undefined,
    isPending: true,
    isError: false,
  } as never);
  vi.mocked(useModelCatalog).mockReturnValue({
    data: { defaultAlias: "qwen-plus", items: [] },
    isPending: false,
    isError: false,
  } as never);
  const setEnabledMutate = overrides.setEnabledMutate ?? vi.fn();
  vi.mocked(useSetAgentEnabled).mockReturnValue({
    mutate: setEnabledMutate,
    isPending: false,
  } as never);
  const onUse = overrides.onUse ?? vi.fn();
  const copy = WORKSPACE_COPY[overrides.locale ?? "en"];
  return {
    onUse,
    setEnabledMutate,
    ...render(
      createElement(AgentsWorkspace, {
        projectId: "project-1",
        copy,
        onUse,
      }),
      { wrapper },
    ),
  };
}

describe("AgentsWorkspace filters", () => {
  it("shows all latest agents by default", () => {
    renderWorkspace();
    expect(screen.getByText("Built-in reviewer")).toBeVisible();
    expect(screen.getByText("Custom helper")).toBeVisible();
    expect(screen.getByText("Retired helper")).toBeVisible();
  });

  it("filters to only built-in agents", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter" }),
      "Built-in",
    );
    expect(screen.getByText("Built-in reviewer")).toBeVisible();
    expect(screen.queryByText("Custom helper")).not.toBeInTheDocument();
    expect(screen.queryByText("Retired helper")).not.toBeInTheDocument();
  });

  it("filters to only disabled agents", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter" }),
      "Disabled",
    );
    expect(screen.getByText("Retired helper")).toBeVisible();
    expect(screen.queryByText("Built-in reviewer")).not.toBeInTheDocument();
  });

  it("shows a no-results message when a filter matches nothing", async () => {
    const user = userEvent.setup();
    renderWorkspace({ agents: [] });
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Filter" }),
      "Disabled",
    );
    expect(screen.getByText("No agents match this filter.")).toBeVisible();
  });
});

describe("AgentsWorkspace actions", () => {
  it("calls onUse with the revision id when Use in chat is clicked", async () => {
    const user = userEvent.setup();
    const onUse = vi.fn();
    renderWorkspace({ onUse });
    const row = screen.getByText("Custom helper").closest("li")!;
    await user.click(
      screen
        .getAllByRole("button", { name: "Use in chat" })
        .find((button) => row.contains(button))!,
    );
    expect(onUse).toHaveBeenCalledWith("rev_custom");
  });

  it("toggles enabled state through Enable/Disable", async () => {
    const user = userEvent.setup();
    const setEnabledMutate = vi.fn();
    renderWorkspace({ setEnabledMutate });
    const row = screen.getByText("Built-in reviewer").closest("li")!;
    await user.click(
      screen
        .getAllByRole("button", { name: "Disable" })
        .find((button) => row.contains(button))!,
    );
    expect(setEnabledMutate).toHaveBeenCalledWith({
      agentId: "agent_builtin",
      enabled: false,
    });
  });

  it("shows a notice on an agent whose bundled Skill has a newer revision", () => {
    renderWorkspace();
    const row = screen.getByText("Custom helper").closest("li")!;
    expect(row).toHaveTextContent("A bundled skill has a newer revision.");
  });

  it("opens the create dialog from New agent", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(screen.getByRole("button", { name: "New agent" }));
    expect(screen.getByRole("dialog", { name: "New agent" })).toBeVisible();
  });
});
