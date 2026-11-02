import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AiAssetError,
  useCreateAgent,
  useReviseAgent,
  useTools,
  type SkillRevision,
  type SkillRevisionDetail,
  type ToolDescriptor,
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
      useTools: vi.fn(),
    };
  },
);

const TOOL_DESCRIPTORS: ToolDescriptor[] = [
  {
    toolId: "knowledge.search",
    name: "Knowledge Search",
    description: "Searches approved knowledge sources.",
    required: true,
    defaultEnabled: true,
  },
  {
    toolId: "knowledge.answer",
    name: "Knowledge Answer",
    description: "Produces a governed, citation-grounded answer.",
    required: true,
    defaultEnabled: true,
  },
  {
    toolId: "graph.relations",
    name: "Graph Relations",
    description: "Explores entity relations.",
    required: false,
    defaultEnabled: true,
  },
  {
    toolId: "insights.query",
    name: "Insights Query",
    description: "Queries authorized Insights metrics.",
    required: false,
    defaultEnabled: true,
  },
];

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

const OTHER_SKILL: SkillRevision = {
  ...APPLICABLE_SKILL,
  revisionId: "skill-rev-3",
  skillId: "skill-3",
  assetId: "skill-3",
  displayName: "Glossary skill",
} as SkillRevision;

const pinnedGroup = () => screen.getByRole("group", { name: "Pinned Skills" });
const excludedGroup = () =>
  screen.getByRole("group", { name: "Excluded Skills" });

const EDIT_AGENT = {
  revisionId: "agent-rev-1",
  assetId: "agent-1",
  agentId: "agent-1",
  displayName: "Underwriting helper",
  description: "Helps with underwriting.",
  contentDigest: "digest-agent",
  toolAllowlist: ["knowledge.search", "knowledge.answer"],
  outputSchemaDigest: "digest-schema",
  sourceKind: "authored",
  builtIn: false,
  status: "enabled",
  isLatest: true,
  defaultModelAlias: null,
  pinnedSkillIds: [],
  excludedSkillIds: [],
  systemInstruction: "Review the evidence.",
} as unknown as NonNullable<Parameters<typeof AgentForm>[0]["agent"]>;

async function fillRequiredFields(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("textbox", { name: "Name" }), "a");
  await user.type(screen.getByRole("textbox", { name: "Description" }), "d");
  await user.type(
    screen.getByRole("textbox", { name: "System instruction" }),
    "s",
  );
}

function skillDetailFor(
  revisionId: string,
  instructionsLength: number,
  status: "enabled" | "disabled" = "enabled",
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
    status,
    isLatest: true,
    contentDigest: "digest",
    files: [],
  } as unknown as SkillRevisionDetail;
}

function mockFetchSkillDetail(
  bySize: Record<string, number>,
  byStatus: Record<string, "enabled" | "disabled"> = {},
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) => {
      const match = /\/skills\/([^/]+)$/.exec(input);
      const revisionId = match?.[1] ?? "";
      const size = bySize[revisionId] ?? 0;
      const status = byStatus[revisionId] ?? "enabled";
      return new Response(
        JSON.stringify(skillDetailFor(revisionId, size, status)),
        { status: 200 },
      );
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
    tools?: readonly ToolDescriptor[];
    toolsError?: boolean;
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
  vi.mocked(useTools).mockReturnValue({
    data:
      overrides.toolsError === true
        ? undefined
        : (overrides.tools ?? TOOL_DESCRIPTORS),
    isLoading: false,
    isError: overrides.toolsError ?? false,
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
  it("required tools are checked and disabled; optional tools default on and toggle into toolIds", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    const createMutateAsync = vi
      .fn()
      .mockResolvedValue({ revisionId: "rev_new" });
    renderForm({ createMutateAsync });

    const search = screen.getByRole("checkbox", { name: "Knowledge Search" });
    const answer = screen.getByRole("checkbox", { name: "Knowledge Answer" });
    const relations = screen.getByRole("checkbox", { name: "Graph Relations" });
    const insights = screen.getByRole("checkbox", { name: "Insights Query" });

    expect(search).toBeChecked();
    expect(search).toBeDisabled();
    expect(answer).toBeChecked();
    expect(answer).toBeDisabled();
    expect(relations).toBeChecked();
    expect(relations).toBeEnabled();
    expect(insights).toBeChecked();
    expect(insights).toBeEnabled();

    await user.click(relations);
    expect(relations).not.toBeChecked();

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
        toolIds: ["knowledge.search", "knowledge.answer", "insights.query"],
      }),
    );
  });

  it("shows a visible error when the tool catalog fails to load, instead of a silently disabled Save (review fix round 1, folded minor)", () => {
    mockFetchSkillDetail({});
    renderForm({ toolsError: true });

    expect(screen.getByText("Tools could not be loaded.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("seeds toolIds from the revision's toolAllowlist on edit; an old allowlist lacking new tools leaves them unchecked", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    const reviseMutateAsync = vi
      .fn()
      .mockResolvedValue({ revisionId: "agent-rev-2" });
    renderForm({
      mode: "edit",
      reviseMutateAsync,
      agent: {
        revisionId: "agent-rev-1",
        assetId: "agent-1",
        agentId: "agent-1",
        displayName: "Underwriting helper",
        description: "Helps with underwriting.",
        contentDigest: "digest-agent",
        // An allowlist authored before `graph.relations`/`insights.query`
        // existed -- it must not retroactively gain either tool just
        // because the current tool catalog now offers them.
        toolAllowlist: ["knowledge.search", "knowledge.answer"],
        outputSchemaDigest: "digest-schema",
        sourceKind: "authored",
        builtIn: false,
        status: "enabled",
        isLatest: true,
        defaultModelAlias: null,
        pinnedSkillIds: [],
        excludedSkillIds: [],
        systemInstruction: "Review the evidence.",
      } as Parameters<typeof AgentForm>[0]["agent"],
    });

    expect(
      screen.getByRole("checkbox", { name: "Knowledge Search" }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Knowledge Answer" }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Graph Relations" }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Insights Query" }),
    ).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(reviseMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        request: expect.objectContaining({
          toolIds: ["knowledge.search", "knowledge.answer"],
        }),
      }),
    );
  });

  it("grays out a Skill that isn't applicable to knowledge answers", () => {
    mockFetchSkillDetail({});
    renderForm({ skills: [APPLICABLE_SKILL, NOT_APPLICABLE_SKILL] });
    for (const group of [pinnedGroup(), excludedGroup()]) {
      expect(
        within(group).getByRole("checkbox", { name: /Citation skill/ }),
      ).toBeEnabled();
      expect(
        within(group).getByRole("checkbox", { name: /Test plan skill/ }),
      ).toBeDisabled();
    }
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

  it("blocks save when the description is over 1,024 characters", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    renderForm();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "a");
    await user.click(screen.getByRole("textbox", { name: "Description" }));
    await user.paste("x".repeat(1025));
    await user.type(
      screen.getByRole("textbox", { name: "System instruction" }),
      "s",
    );
    expect(
      screen.getByText("Description is over the 1,024 character limit."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("blocks save when the system instruction is over 32 KB", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    renderForm();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "a");
    await user.type(screen.getByRole("textbox", { name: "Description" }), "d");
    await user.click(
      screen.getByRole("textbox", { name: "System instruction" }),
    );
    await user.paste("a".repeat(32 * 1024 + 1));
    expect(
      screen.getByText("System instruction is over the 32 KB limit."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("filters the Skill list by the search box", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    renderForm({ skills: [APPLICABLE_SKILL, NOT_APPLICABLE_SKILL] });
    await user.type(
      screen.getByRole("textbox", { name: "Search skills" }),
      "Test plan",
    );
    expect(
      screen.queryByRole("checkbox", { name: /Citation skill/ }),
    ).not.toBeInTheDocument();
    expect(
      within(pinnedGroup()).getByRole("checkbox", { name: /Test plan skill/ }),
    ).toBeVisible();
    expect(
      within(excludedGroup()).getByRole("checkbox", {
        name: /Test plan skill/,
      }),
    ).toBeVisible();
  });
});

describe("AgentForm Skill budget", () => {
  it("warns at 90% of the per-turn Skill budget (summing each pinned Skill's latest revision) without blocking save", async () => {
    const user = userEvent.setup();
    const nearBudgetSize = Math.ceil(AGENT_SKILLS_BUDGET_BYTES * 0.95);
    mockFetchSkillDetail({ "skill-rev-1": nearBudgetSize });
    renderForm({ skills: [APPLICABLE_SKILL] });

    await user.click(
      within(pinnedGroup()).getByRole("checkbox", { name: /Citation skill/ }),
    );
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

    await user.click(
      within(pinnedGroup()).getByRole("checkbox", { name: /Citation skill/ }),
    );
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

  it("submits pinned and excluded skill ids", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({ "skill-rev-1": 10 });
    const createMutateAsync = vi
      .fn()
      .mockResolvedValue({ revisionId: "rev_new" });
    renderForm({
      createMutateAsync,
      skills: [APPLICABLE_SKILL, OTHER_SKILL],
    });

    await user.click(
      within(pinnedGroup()).getByRole("checkbox", { name: /Citation skill/ }),
    );
    await user.click(
      within(excludedGroup()).getByRole("checkbox", {
        name: /Glossary skill/,
      }),
    );
    await fillRequiredFields(user);
    const save = screen.getByRole("button", { name: "Save" });
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);

    const draft = createMutateAsync.mock.calls[0]![0] as Record<
      string,
      unknown
    >;
    // By Skill, not by revision, and never the deprecated revision list.
    expect(draft.pinnedSkillIds).toEqual(["skill-1"]);
    expect(draft.excludedSkillIds).toEqual(["skill-3"]);
    expect(draft).not.toHaveProperty("skillRevisionIds");
  });

  it("re-sends the existing pinned and excluded skill ids on edit (the revision is a full replacement)", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({ "skill-rev-1": 10 });
    const reviseMutateAsync = vi
      .fn()
      .mockResolvedValue({ revisionId: "agent-rev-2" });
    renderForm({
      mode: "edit",
      reviseMutateAsync,
      skills: [APPLICABLE_SKILL, OTHER_SKILL],
      agent: {
        ...EDIT_AGENT,
        pinnedSkillIds: ["skill-1"],
        excludedSkillIds: ["skill-3"],
      },
    });

    expect(
      within(pinnedGroup()).getByRole("checkbox", { name: /Citation skill/ }),
    ).toBeChecked();
    expect(
      within(excludedGroup()).getByRole("checkbox", { name: /Glossary skill/ }),
    ).toBeChecked();
    const save = screen.getByRole("button", { name: "Save" });
    await waitFor(() => expect(save).toBeEnabled());
    await user.click(save);

    const call = reviseMutateAsync.mock.calls[0]![0] as {
      request: Record<string, unknown>;
    };
    expect(call.request.pinnedSkillIds).toEqual(["skill-1"]);
    expect(call.request.excludedSkillIds).toEqual(["skill-3"]);
    expect(call.request).not.toHaveProperty("skillRevisionIds");
  });

  it("a pinned skill is disabled in the excluded picker", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({ "skill-rev-1": 10 });
    renderForm({ skills: [APPLICABLE_SKILL, OTHER_SKILL] });

    await user.click(
      within(pinnedGroup()).getByRole("checkbox", { name: /Citation skill/ }),
    );
    expect(
      within(excludedGroup()).getByRole("checkbox", {
        name: /Citation skill/,
      }),
    ).toBeDisabled();

    // And vice versa.
    await user.click(
      within(excludedGroup()).getByRole("checkbox", {
        name: /Glossary skill/,
      }),
    );
    expect(
      within(pinnedGroup()).getByRole("checkbox", { name: /Glossary skill/ }),
    ).toBeDisabled();
  });

  it.each([
    ["agent_skill_conflict", "A skill can't be both pinned and excluded."],
    [
      "agent_skill_limit_exceeded",
      "An agent can pin or exclude at most 64 skills each. Remove some before saving.",
    ],
  ])("maps %s to a product message", async (code, message) => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    const createMutateAsync = vi
      .fn()
      .mockRejectedValue(new AiAssetError({ code, detail: "rejected" }));
    renderForm({ createMutateAsync });

    await fillRequiredFields(user);
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText(message)).toBeVisible();
  });

  it("shows a pinned Skill that was disabled with a warning and a remove action, instead of blocking Save (I-6)", async () => {
    const user = userEvent.setup();
    mockFetchSkillDetail({});
    const reviseMutateAsync = vi
      .fn()
      .mockResolvedValue({ revisionId: "agent-rev-2" });
    renderForm({
      mode: "edit",
      skills: [
        APPLICABLE_SKILL,
        {
          ...OTHER_SKILL,
          displayName: "Retired skill",
          status: "disabled",
        } as SkillRevision,
      ],
      reviseMutateAsync,
      agent: { ...EDIT_AGENT, pinnedSkillIds: ["skill-3"] },
    });

    const warning = screen.getByText(
      "This skill has been disabled and will be skipped when the agent answers.",
    );
    expect(warning).toBeVisible();
    expect(within(pinnedGroup()).getByText("Retired skill")).toBeVisible();
    // A disabled Skill isn't offered as a new pick.
    expect(
      within(excludedGroup()).queryByRole("checkbox", {
        name: /Retired skill/,
      }),
    ).not.toBeInTheDocument();
    const save = screen.getByRole("button", { name: "Save" });
    await waitFor(() => expect(save).toBeEnabled());

    await user.click(save);
    expect(reviseMutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        agentId: "agent-1",
        request: expect.objectContaining({ pinnedSkillIds: ["skill-3"] }),
      }),
    );

    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(
      screen.queryByText(
        "This skill has been disabled and will be skipped when the agent answers.",
      ),
    ).toBeNull();
  });
});
