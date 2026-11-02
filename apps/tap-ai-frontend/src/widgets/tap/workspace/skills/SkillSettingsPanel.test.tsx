import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SkillHubDetail } from "../../../../features/knowledge/api/skillHub";
import { WORKSPACE_COPY } from "../copy";
import { SkillSettingsPanel } from "./SkillSettingsPanel";

function hubDetail(
  over: Partial<SkillHubDetail["skill"]> = {},
): SkillHubDetail {
  return {
    skill: {
      skillId: "skill_1",
      name: "BDD Scenario Design",
      description: "Turns requirements into scenarios.",
      category: "test-design",
      version: "1.2.0",
      revisionNumber: 4,
      latestRevisionId: "rev_4",
      sourceKind: "authored",
      originKind: "authored",
      originRef: null,
      originImportedAt: null,
      originActorId: "alice",
      ownerActorId: "alice",
      ownerAssignedAt: "2026-10-01T09:00:00Z",
      status: "enabled",
      applicableTasks: ["knowledge.answer"],
      compatibility: null,
      updatedAt: "2026-10-20T12:00:00Z",
      builtIn: false,
      ...over,
    },
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
  };
}

type Reply = { status: number; body: unknown };

function stubFetch(reply: (url: string, init?: RequestInit) => Reply) {
  const fetchMock = vi.fn((input: string, init?: RequestInit) => {
    const { status, body } = reply(String(input), init);
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPanel(
  detail: SkillHubDetail,
  reply: (url: string, init?: RequestInit) => Reply = () => ({
    status: 200,
    body: detail.skill,
  }),
) {
  const fetchMock = stubFetch(reply);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onEdit = vi.fn();
  render(
    createElement(
      QueryClientProvider,
      { client },
      createElement(SkillSettingsPanel, {
        projectId: "project-1",
        detail,
        copy: WORKSPACE_COPY.en,
        onEdit,
      }),
    ) as ReactNode,
  );
  return { fetchMock, onEdit };
}

function callsTo(fetchMock: ReturnType<typeof stubFetch>, suffix: string) {
  return fetchMock.mock.calls.filter((call) =>
    String(call[0]).endsWith(suffix),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("SkillSettingsPanel", () => {
  it("saves a new category with PUT profile", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPanel(hubDetail());
    const select = screen.getByRole("combobox", { name: "Category" });
    expect(select).toHaveValue("test-design");
    await user.selectOptions(select, "analysis");
    await waitFor(() =>
      expect(callsTo(fetchMock, "/skills/skill_1/profile")).toHaveLength(1),
    );
    const [url, init] = callsTo(fetchMock, "/skills/skill_1/profile")[0]!;
    expect(String(url)).toBe(
      "/api/v1/projects/project-1/ai/skills/skill_1/profile",
    );
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ category: "analysis" });
  });

  it("transfers the owner with POST owner", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPanel(hubDetail());
    expect(screen.getByText("alice")).toBeVisible();
    await user.type(screen.getByRole("textbox", { name: "New owner" }), "bob");
    await user.click(screen.getByRole("button", { name: "Transfer" }));
    await waitFor(() =>
      expect(callsTo(fetchMock, "/skills/skill_1/owner")).toHaveLength(1),
    );
    const [, init] = callsTo(fetchMock, "/skills/skill_1/owner")[0]!;
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ newOwnerActorId: "bob" });
  });

  it("shows an error when the new owner does not exist (actor_not_found)", async () => {
    const user = userEvent.setup();
    renderPanel(hubDetail(), (url) =>
      url.endsWith("/owner")
        ? {
            status: 404,
            body: { code: "actor_not_found", detail: "actor not found" },
          }
        : { status: 200, body: {} },
    );
    await user.type(
      screen.getByRole("textbox", { name: "New owner" }),
      "nobody",
    );
    await user.click(screen.getByRole("button", { name: "Transfer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No user with this ID was found in this project.",
    );
  });

  it("disables an enabled skill", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPanel(hubDetail());
    await user.click(screen.getByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(callsTo(fetchMock, "/skills/skill_1/disable")).toHaveLength(1),
    );
    expect(callsTo(fetchMock, "/skills/skill_1/disable")[0]![1]?.method).toBe(
      "POST",
    );
  });

  it("enables a disabled skill", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPanel(hubDetail({ status: "disabled" }));
    await user.click(screen.getByRole("button", { name: "Enable" }));
    await waitFor(() =>
      expect(callsTo(fetchMock, "/skills/skill_1/enable")).toHaveLength(1),
    );
  });

  it("starts a new revision from settings", async () => {
    const user = userEvent.setup();
    const { onEdit } = renderPanel(hubDetail());
    await user.click(screen.getByRole("button", { name: "New revision" }));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });

  it("points an imported skill at re-importing instead of a new revision", () => {
    renderPanel(hubDetail({ sourceKind: "imported", originKind: "zip" }));
    expect(screen.queryByRole("button", { name: "New revision" })).toBeNull();
    expect(
      screen.getByText(
        "Imported skills can't be edited in place — import the updated package as a new revision instead.",
      ),
    ).toBeVisible();
  });

  // Ruling: built-in Skills keep enable/disable (as before), but category,
  // owner transfer and new revision are read-only for them.
  it("内置 Skill 设置分页无可写控件 — built-in skill has no category, owner or new-revision controls (enable/disable stays)", () => {
    renderPanel(
      hubDetail({
        builtIn: true,
        sourceKind: "built_in",
        originKind: "built_in",
        ownerActorId: "system",
      }),
    );
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Transfer" })).toBeNull();
    expect(screen.queryByRole("button", { name: "New revision" })).toBeNull();
    expect(screen.getByText("Test design")).toBeVisible();
    expect(
      screen.getByText(
        "Built-in skills are maintained by Tapper. You can only enable or disable them.",
      ),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Disable" })).toBeVisible();
  });
});
