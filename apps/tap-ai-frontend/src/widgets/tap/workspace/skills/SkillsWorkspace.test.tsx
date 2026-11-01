import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  useSkill,
  useSkillRevisions,
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
      useSkill: vi.fn(),
      useSkillRevisions: vi.fn(),
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

function catalogItem(over: Record<string, unknown>) {
  return {
    skill: {
      skillId: "skill_custom",
      name: "Custom policy helper",
      description: "Answers policy questions.",
      category: "knowledge",
      version: "r2",
      revisionNumber: 2,
      latestRevisionId: "rev_custom",
      sourceKind: "authored",
      originKind: "authored",
      originRef: null,
      originImportedAt: null,
      ownerActorId: "alice",
      ownerAssignedAt: "2026-10-01T09:00:00Z",
      status: "enabled",
      applicableTasks: ["knowledge.answer"],
      compatibility: null,
      updatedAt: "2026-10-20T12:00:00Z",
      builtIn: false,
      ...over,
    },
    activations: 3,
    byMode: { manual: 1, pinned: 1, auto: 1 },
  };
}

const CATALOG = [
  catalogItem({}),
  catalogItem({
    skillId: "skill_builtin",
    name: "Built-in reviewer",
    latestRevisionId: "rev_builtin",
    sourceKind: "built_in",
    builtIn: true,
  }),
];

function stubSkillHub(items: unknown[]) {
  const envelope = (data: unknown) => ({
    runtimeStatus: "ok",
    computedAt: "2026-10-30T08:00:00Z",
    timezone: "UTC",
    period: { from: "2026-10-01", to: "2026-10-30" },
    granularity: "day",
    data,
  });
  const detailOf = (url: string) => {
    const match = /\/skill-hub\/catalog\/([^/?]+)/.exec(url);
    if (match === null) return undefined;
    const item = items.find(
      (entry) =>
        (entry as ReturnType<typeof catalogItem>).skill.skillId === match[1],
    ) as ReturnType<typeof catalogItem> | undefined;
    if (url.includes("/usage"))
      return envelope({
        byMode: { manual: 0, pinned: 0, auto: 0 },
        points: [],
      });
    return envelope({
      skill: item?.skill,
      usage: {
        byMode: { manual: 0, pinned: 0, auto: 0 },
        users: 0,
        satisfaction: null,
        satisfactionSample: "insufficient",
        lastUsedAt: null,
        pinnedByAgents: 0,
        runtime: null,
      },
      revisions: [],
      files: [],
      pinnedBy: [],
      topAgents: [],
      topCallers: [],
    });
  };
  const fetchMock = vi.fn((input: string) =>
    Promise.resolve(
      new Response(
        JSON.stringify(
          detailOf(String(input)) ??
            (String(input).includes("/skill-hub/summary")
              ? envelope({
                  skillTotal: items.length,
                  enabled: items.length,
                  usedInPeriod: items.length,
                  unused: 0,
                  autoShare: null,
                  candidateCount: items.length,
                  candidateLimit: 60,
                })
              : envelope(items)),
        ),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderWorkspace(
  overrides: {
    items?: unknown[];
    locale?: "en" | "zh";
    onUse?: (revisionId: string) => void;
  } = {},
) {
  const fetchMock = stubSkillHub(overrides.items ?? CATALOG);
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
  const onUse = overrides.onUse ?? vi.fn();
  const copy = WORKSPACE_COPY[overrides.locale ?? "en"];
  return {
    onUse,
    fetchMock,
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

afterEach(() => vi.unstubAllGlobals());

describe("SkillsWorkspace library", () => {
  it("lists the project's skills from the library for the last 30 days", async () => {
    const { fetchMock } = renderWorkspace();
    expect(await screen.findByText("Custom policy helper")).toBeVisible();
    expect(screen.getByText("Built-in reviewer")).toBeVisible();
    const catalogUrl = fetchMock.mock.calls
      .map((call) => String(call[0]))
      .find((url) => url.includes("/skill-hub/catalog"))!;
    const params = new URL(catalogUrl, "http://localhost").searchParams;
    const from = Date.parse(`${params.get("from")}T00:00:00Z`);
    const to = Date.parse(`${params.get("to")}T00:00:00Z`);
    expect((to - from) / 86_400_000).toBe(29);
  });

  it("opens the skill's latest revision in the detail drawer", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(
      await screen.findByRole("button", { name: "Custom policy helper" }),
    );
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(vi.mocked(useSkill)).toHaveBeenLastCalledWith(
      "project-1",
      "rev_custom",
    );
  });
});

describe("SkillsWorkspace actions", () => {
  it("uses a skill in chat from its detail drawer", async () => {
    const user = userEvent.setup();
    const onUse = vi.fn();
    const { fetchMock } = renderWorkspace({ onUse });
    const detail = {
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
      description: "Answers policy questions.",
      instructions: "Answer from the policy index.",
      attachedReferences: null,
      files: [],
      frontmatter: { name: "custom-policy-helper" },
    };
    vi.mocked(useSkill).mockImplementation(
      (_projectId: string, revisionId: string | null) =>
        (revisionId === "rev_custom"
          ? { data: detail, isPending: false, isError: false }
          : { data: undefined, isPending: true, isError: false }) as never,
    );
    vi.mocked(useSkillRevisions).mockReturnValue({
      data: [detail],
      isPending: false,
      isError: false,
    } as never);
    await user.click(
      await screen.findByRole("button", { name: "Custom policy helper" }),
    );
    const drawer = screen.getByRole("dialog", { name: "Custom policy helper" });
    await user.click(
      within(drawer).getByRole("button", { name: "Use in chat" }),
    );
    expect(onUse).toHaveBeenCalledWith("rev_custom");
    // The drawer reads the skill's details for the library's period.
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    const catalogParams = new URL(
      urls.find((url) => url.includes("/skill-hub/catalog?"))!,
      "http://localhost",
    ).searchParams;
    const detailUrl = urls.find((url) =>
      url.includes("/skill-hub/catalog/skill_custom?"),
    );
    expect(detailUrl).toBeDefined();
    const detailParams = new URL(detailUrl!, "http://localhost").searchParams;
    expect(detailParams.get("from")).toBe(catalogParams.get("from"));
    expect(detailParams.get("to")).toBe(catalogParams.get("to"));
  });

  it("opens the import dialog from the Import skill button", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(screen.getByRole("button", { name: "Import skill" }));
    expect(screen.getByText("Import a skill")).toBeVisible();
  });

  it("opens the import dialog from the empty library", async () => {
    const user = userEvent.setup();
    renderWorkspace({ items: [] });
    const empty = (
      await screen.findByText("Your skill library is empty")
    ).closest("div")!;
    await user.click(
      within(empty).getByRole("button", { name: "Import skill" }),
    );
    expect(screen.getByText("Import a skill")).toBeVisible();
  });

  it("opens the create form from the New skill button", async () => {
    const user = userEvent.setup();
    renderWorkspace();
    await user.click(screen.getAllByRole("button", { name: "New skill" })[0]!);
    expect(screen.getByText("New skill", { selector: "h2" })).toBeVisible();
  });
});

describe("SkillsWorkspace locale", () => {
  it("renders Chinese copy", async () => {
    renderWorkspace({ locale: "zh" });
    expect(screen.getByText("技能")).toBeVisible();
    expect(screen.getByRole("button", { name: "导入 Skill" })).toBeVisible();
    expect(screen.getByRole("button", { name: "新建 Skill" })).toBeVisible();
    expect(await screen.findByRole("region", { name: "库概览" })).toBeVisible();
  });
});
