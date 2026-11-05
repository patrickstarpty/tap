import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

const PERIOD = { from: "2026-10-01", to: "2026-10-30" };

function hubSkill(over: Record<string, unknown> = {}) {
  return {
    skillId: "skill_1",
    name: "policy-lookup",
    description: "Looks up policy terms in the product library.",
    category: "knowledge",
    version: "1.2.0",
    revisionNumber: 4,
    latestRevisionId: "rev_1",
    sourceKind: "imported",
    originKind: "zip",
    originRef: "policy-lookup.zip",
    originImportedAt: "2026-10-02T09:30:00Z",
    originActorId: "bob",
    ownerActorId: "alice",
    ownerAssignedAt: "2026-10-02T09:30:00Z",
    status: "enabled",
    applicableTasks: ["knowledge.answer"],
    compatibility: "Requires the policy index",
    updatedAt: "2026-10-20T12:00:00Z",
    builtIn: false,
    ...over,
  };
}

let hubSkillOverrides: Record<string, unknown> = {};

function hubEnvelope(data: unknown) {
  return {
    runtimeStatus: "syncing",
    computedAt: "2026-10-30T08:00:00Z",
    timezone: "UTC",
    period: PERIOD,
    granularity: "day",
    data,
  };
}

beforeEach(() => {
  hubSkillOverrides = {};
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      const url = String(input);
      const body = url.includes("/skill-hub/catalog/skill_1/usage")
        ? hubEnvelope({ byMode: { manual: 1, pinned: 0, auto: 2 }, points: [] })
        : url.includes("/skill-hub/catalog/skill_1")
          ? hubEnvelope({
              skill: hubSkill(hubSkillOverrides),
              usage: {
                byMode: { manual: 1, pinned: 0, auto: 2 },
                users: 2,
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
            })
          : { items: [] };
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

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
  attachedReferences: null as string | null,
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
        period: PERIOD,
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
  it("renders the six tabs and switches between them", async () => {
    const user = userEvent.setup();
    renderDrawer();
    const names = [
      "Overview",
      "Instructions",
      "Files",
      "Revision history",
      "Usage",
      "Settings",
    ];
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(
      names,
    );
    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    for (const name of names) {
      await user.click(screen.getByRole("tab", { name }));
      expect(screen.getByRole("tab", { name })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
    }
    expect(screen.queryByRole("tab", { name: "Metadata" })).toBeNull();
  });

  it("shows the skill's basic information on the Overview tab", async () => {
    renderDrawer();
    const panel = screen.getByRole("tabpanel");
    expect(
      await within(panel).findByText(
        "Looks up policy terms in the product library.",
      ),
    ).toBeVisible();
    const row = (label: string) =>
      within(panel).getByText(label, { selector: "dt" }).nextElementSibling;
    expect(row("Category")).toHaveTextContent("Knowledge");
    expect(row("Version")).toHaveTextContent("1.2.0");
    expect(row("Source")).toHaveTextContent("Imported");
    expect(row("Imported as")).toHaveTextContent("Zip package");
    expect(row("File or package")).toHaveTextContent("policy-lookup.zip");
    expect(row("Imported at")).toHaveTextContent("10/02/2026, 09:30");
    expect(row("Imported by")).toHaveTextContent("bob");
    expect(within(panel).queryByText("Created by")).toBeNull();
    expect(row("Owner")).toHaveTextContent("alice");
    expect(row("Compatibility")).toHaveTextContent("Requires the policy index");
    expect(row("Applicable tasks")).toHaveTextContent("Knowledge answers");
  });

  it("names the creator and no import method for an authored skill", async () => {
    hubSkillOverrides = { sourceKind: "authored", originKind: "authored" };
    renderDrawer();
    const panel = screen.getByRole("tabpanel");
    const createdBy = await within(panel).findByText("Created by", {
      selector: "dt",
    });
    expect(createdBy.nextElementSibling).toHaveTextContent("bob");
    expect(within(panel).queryByText("Imported by")).toBeNull();
    expect(within(panel).queryByText("Imported at")).toBeNull();
    // The import-method row is only for imported skills.
    expect(within(panel).queryByText("Imported as")).toBeNull();
  });

  it("shows instructions content on the Instructions tab", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("tab", { name: "Instructions" }));
    expect(screen.getByText("Search the index.")).toBeVisible();
  });

  it("shows the usage panel with the detail's run-data status", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("tab", { name: "Usage" }));
    expect(await screen.findByText("Run data is syncing")).toBeVisible();
    expect(screen.getByText("Insufficient sample")).toBeVisible();
  });

  it("shows the settings panel and starts a new revision from it", async () => {
    const user = userEvent.setup();
    hubSkillOverrides = { sourceKind: "authored", originKind: "authored" };
    const customDetail = {
      ...BUILT_IN_DETAIL,
      sourceKind: "authored",
      builtIn: false,
    };
    const onEdit = vi.fn();
    renderDrawer({ detail: customDetail, onEdit });
    await user.click(screen.getByRole("tab", { name: "Settings" }));
    await user.click(
      await screen.findByRole("button", { name: "New revision" }),
    );
    expect(onEdit).toHaveBeenCalledWith(customDetail);
  });

  it("compares two revisions from the Revision history tab", async () => {
    const user = userEvent.setup();
    renderDrawer();
    await user.click(screen.getByRole("tab", { name: "Revision history" }));
    expect(
      screen.getByRole("combobox", { name: "Left revision" }),
    ).toBeVisible();
    expect(
      screen.getByRole("combobox", { name: "Right revision" }),
    ).toBeVisible();
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

  it("does not offer Edit for an imported skill, and points at re-importing instead (I-3)", () => {
    const importedDetail = {
      ...BUILT_IN_DETAIL,
      sourceKind: "imported",
      builtIn: false,
    };
    renderDrawer({ detail: importedDetail });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy as custom" })).toBeNull();
    expect(
      screen.getByText(
        "Imported skills can't be edited in place — import the updated package as a new revision instead.",
      ),
    ).toBeVisible();
  });

  it("shows attached reference text and a fallback when none was attached (I-7)", async () => {
    const user = userEvent.setup();
    const { unmount } = renderDrawer({
      detail: { ...BUILT_IN_DETAIL, attachedReferences: "Cited excerpt." },
    });
    await user.click(screen.getByRole("tab", { name: "Instructions" }));
    expect(screen.getByText("Cited excerpt.")).toBeVisible();
    unmount();

    renderDrawer({ detail: { ...BUILT_IN_DETAIL, attachedReferences: null } });
    await user.click(screen.getByRole("tab", { name: "Instructions" }));
    expect(screen.getByText("No reference text was attached.")).toBeVisible();
  });

  it("labels each file by category, marks non-prompt files 'Saved, not executed', and links a download (I-7)", async () => {
    const user = userEvent.setup();
    renderDrawer({
      detail: {
        ...BUILT_IN_DETAIL,
        files: [
          {
            relativePath: "SKILL.md",
            category: "skill",
            attached: true,
            sizeBytes: 1,
            sha256: "a",
          },
          {
            relativePath: "scripts/run.py",
            category: "script",
            attached: false,
            sizeBytes: 1,
            sha256: "b",
          },
          {
            relativePath: "references/notes.md",
            category: "reference",
            attached: true,
            sizeBytes: 1,
            sha256: "c",
          },
        ],
      },
    });
    await user.click(screen.getByRole("tab", { name: "Files" }));

    expect(screen.getByText("Script · saved, not executed")).toBeVisible();
    expect(screen.getByText("Reference · attached")).toBeVisible();
    expect(screen.getByText("Saved, not executed")).toBeVisible();
    const downloadLinks = screen.getAllByRole("link", { name: "Download" });
    expect(downloadLinks.length).toBe(3);
    expect(downloadLinks[1]!.getAttribute("href")).toBe(
      "/api/v1/projects/project-1/ai/skills/rev_1/files/scripts/run.py",
    );
  });

  it("lets a revision history entry switch the drawer to that revision (I-7)", async () => {
    const user = userEvent.setup();
    const olderRevision = { ...BUILT_IN_DETAIL, revisionId: "rev_0" };
    renderDrawer({
      revisions: [olderRevision, BUILT_IN_DETAIL],
    });
    await user.click(screen.getByRole("tab", { name: "Revision history" }));

    const switchButton = screen.getByRole("button", { name: "rev_0" });
    await user.click(switchButton);

    expect(useSkill).toHaveBeenLastCalledWith("project-1", "rev_0");
  });

  it("hides Use in chat and Edit while viewing an older revision, since they must act on the viewed revision, not silently on the latest one (minor)", async () => {
    // A real `useSkill(projectId, activeRevisionId)` call re-fetches per
    // revision id -- unlike `renderDrawer`'s static mock, this needs a
    // dynamic one so switching revisions actually changes `detail`
    // (including `isLatest`).
    const user = userEvent.setup();
    const latest = {
      ...BUILT_IN_DETAIL,
      sourceKind: "authored",
      builtIn: false,
    };
    const older = { ...latest, revisionId: "rev_0", isLatest: false };
    vi.mocked(useSkill).mockImplementation(
      (_projectId: string, revisionId: string | null) =>
        ({
          data: revisionId === "rev_0" ? older : latest,
          isPending: false,
          isError: false,
        }) as never,
    );
    vi.mocked(useSkillRevisions).mockReturnValue({
      data: [older, latest],
      isPending: false,
      isError: false,
    } as never);
    render(
      createElement(SkillDetailDrawer, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        revisionId: "rev_1",
        period: PERIOD,
        onClose: vi.fn(),
        onUse: vi.fn(),
        onEdit: vi.fn(),
        onCopyAsCustom: vi.fn(),
        opener: document.body,
      }),
      { wrapper },
    );

    expect(screen.getByRole("button", { name: "Use in chat" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Edit" })).toBeVisible();

    await user.click(screen.getByRole("tab", { name: "Revision history" }));
    await user.click(screen.getByRole("button", { name: "rev_0" }));

    expect(
      screen.queryByRole("button", { name: "Use in chat" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Edit" }),
    ).not.toBeInTheDocument();
  });

  it("shows a retry-able error instead of a blank drawer when the detail fails to load", async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    vi.mocked(useSkill).mockReturnValue({
      data: undefined,
      isPending: false,
      isError: true,
      refetch,
    } as never);
    vi.mocked(useSkillRevisions).mockReturnValue({
      data: [],
      isPending: false,
      isError: false,
    } as never);

    render(
      createElement(SkillDetailDrawer, {
        projectId: "project-1",
        copy: WORKSPACE_COPY.en,
        revisionId: "rev_1",
        period: PERIOD,
        onClose: vi.fn(),
        onUse: vi.fn(),
        onEdit: vi.fn(),
        onCopyAsCustom: vi.fn(),
        opener: document.body,
      }),
      { wrapper },
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "This skill could not be loaded.",
    );
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalled();
  });
});
