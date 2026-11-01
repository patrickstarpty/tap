import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createElement, useState, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WORKSPACE_COPY } from "../copy";
import type { PeriodSelection } from "../agents/team/PeriodPicker";
import { SkillLibrary } from "./SkillLibrary";

const period = { from: "2026-10-01", to: "2026-10-30" };

function skill(over: Record<string, unknown>) {
  return {
    skillId: "bdd-scenario-design",
    name: "BDD Scenario Design",
    description: "Turns requirements into Given/When/Then scenarios.",
    category: "test-design",
    version: "1.2.0",
    revisionNumber: 4,
    latestRevisionId: "rev_bdd_4",
    sourceKind: "imported",
    originKind: "zip",
    originRef: "bdd.zip",
    originImportedAt: "2026-10-01T09:00:00Z",
    ownerActorId: "alice",
    ownerAssignedAt: "2026-10-01T09:00:00Z",
    status: "enabled",
    applicableTasks: ["knowledge.answer"],
    compatibility: null,
    updatedAt: "2026-10-20T12:00:00Z",
    builtIn: false,
    ...over,
  };
}

const ITEMS = [
  {
    skill: skill({}),
    activations: 12,
    byMode: { manual: 3, pinned: 4, auto: 5 },
  },
  {
    skill: skill({
      skillId: "release-notes",
      name: "Release Notes Writer",
      description: "Drafts release notes from merged changes.",
      category: "writing",
      version: "r3",
      latestRevisionId: "rev_notes_3",
      sourceKind: "authored",
      status: "disabled",
      updatedAt: "2026-10-12T12:00:00Z",
    }),
    activations: 0,
    byMode: { manual: 0, pinned: 0, auto: 0 },
  },
];

function envelope(data: unknown, runtimeStatus = "ok") {
  return {
    runtimeStatus,
    computedAt: "2026-10-30T08:00:00Z",
    timezone: "UTC",
    period,
    granularity: "day",
    data,
  };
}

const SUMMARY = {
  skillTotal: 14,
  enabled: 11,
  usedInPeriod: 7,
  unused: 4,
  autoShare: 0.42,
  candidateCount: 11,
  candidateLimit: 60,
};

interface Responses {
  items?: unknown[];
  summary?: Partial<typeof SUMMARY>;
  runtimeStatus?: string;
}

function stubApi(responses: Responses = {}) {
  const fetchMock = vi.fn((input: string) => {
    const url = String(input);
    const body = url.includes("/skill-hub/summary")
      ? envelope({ ...SUMMARY, ...responses.summary })
      : envelope(responses.items ?? ITEMS, responses.runtimeStatus);
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Query parameters of the latest catalog request. */
function lastCatalogParams(fetchMock: ReturnType<typeof stubApi>) {
  const urls = fetchMock.mock.calls
    .map((call) => String(call[0]))
    .filter((url) => url.includes("/skill-hub/catalog"));
  const last = urls.at(-1);
  if (last === undefined) return {};
  return Object.fromEntries(new URL(last, "http://localhost").searchParams);
}

function Harness(props: {
  locale: "en" | "zh";
  onOpen: (revisionId: string) => void;
  onImport: () => void;
  onCreate: () => void;
}) {
  const [selection, setSelection] = useState<PeriodSelection>({
    preset: "custom",
    custom: period,
  });
  return createElement(SkillLibrary, {
    projectId: "project-1",
    copy: WORKSPACE_COPY[props.locale],
    selection,
    onSelectionChange: setSelection,
    onOpen: props.onOpen,
    onImport: props.onImport,
    onCreate: props.onCreate,
  });
}

function renderLibrary(responses: Responses = {}, locale: "en" | "zh" = "en") {
  const fetchMock = stubApi(responses);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
  const onOpen = vi.fn();
  const onImport = vi.fn();
  const onCreate = vi.fn();
  render(createElement(Harness, { locale, onOpen, onImport, onCreate }), {
    wrapper,
  });
  return { fetchMock, onOpen, onImport, onCreate };
}

afterEach(() => vi.unstubAllGlobals());

describe("SkillLibrary filters and sorting", () => {
  it("requests the period with the default sort", async () => {
    const { fetchMock } = renderLibrary();
    await screen.findByText("BDD Scenario Design");
    expect(lastCatalogParams(fetchMock)).toEqual({
      from: "2026-10-01",
      to: "2026-10-30",
      sort: "activations",
      order: "desc",
    });
  });

  it("searches by text", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderLibrary();
    await screen.findByText("BDD Scenario Design");
    await user.type(
      screen.getByRole("searchbox", { name: "Search skills" }),
      "scenario",
    );
    await waitFor(() =>
      expect(lastCatalogParams(fetchMock)).toMatchObject({ q: "scenario" }),
    );
  });

  it("filters by category, task, source and status", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderLibrary();
    await screen.findByText("BDD Scenario Design");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Category" }),
      "Test design",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Task" }),
      "Knowledge answers",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Source" }),
      "Imported",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Status" }),
      "Disabled",
    );
    await waitFor(() =>
      expect(lastCatalogParams(fetchMock)).toMatchObject({
        category: "test-design",
        task: "knowledge.answer",
        source: "imported",
        status: "disabled",
      }),
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Category" }),
      "All categories",
    );
    await waitFor(() =>
      expect(lastCatalogParams(fetchMock)).not.toHaveProperty("category"),
    );
  });

  it("changes the sort field and order", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderLibrary();
    await screen.findByText("BDD Scenario Design");
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Sort by" }),
      "Name (A–Z)",
    );
    await waitFor(() =>
      expect(lastCatalogParams(fetchMock)).toMatchObject({
        sort: "name",
        order: "asc",
      }),
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Sort by" }),
      "Recently used",
    );
    await waitFor(() =>
      expect(lastCatalogParams(fetchMock)).toMatchObject({
        sort: "last_used_at",
        order: "desc",
      }),
    );
  });
});

describe("SkillLibrary content", () => {
  it("shows each skill's card details", async () => {
    renderLibrary();
    const bdd = await screen.findByRole("listitem", {
      name: "BDD Scenario Design",
    });
    const card = within(bdd);
    expect(
      card.getByText("Turns requirements into Given/When/Then scenarios."),
    ).toBeVisible();
    expect(card.getByText("Test design")).toBeVisible();
    expect(card.getByText("1.2.0")).toBeVisible();
    expect(card.getByText("Imported")).toBeVisible();
    expect(card.getByText("10/20/2026")).toBeVisible();
    expect(card.getByText("12")).toBeVisible();
    expect(card.getByText("Enabled")).toBeVisible();

    const notes = within(
      screen.getByRole("listitem", { name: "Release Notes Writer" }),
    );
    expect(notes.getByText("Writing")).toBeVisible();
    expect(notes.getByText("r3")).toBeVisible();
    expect(notes.getByText("Created in Tapper")).toBeVisible();
    expect(notes.getByText("0")).toBeVisible();
    expect(notes.getByText("Disabled")).toBeVisible();
  });

  it("opens a skill's latest revision from its card", async () => {
    const user = userEvent.setup();
    const { onOpen } = renderLibrary();
    await user.click(
      await screen.findByRole("button", { name: "Release Notes Writer" }),
    );
    expect(onOpen).toHaveBeenCalledWith("rev_notes_3");
  });

  it("shows the library summary", async () => {
    renderLibrary();
    const strip = within(
      await screen.findByRole("region", { name: "Library summary" }),
    );
    expect(await strip.findByText("14")).toBeVisible();
    expect(strip.getByText("Skills")).toBeVisible();
    expect(strip.getByText("11")).toBeVisible();
    expect(strip.getByText("7")).toBeVisible();
    expect(strip.getByText("4")).toBeVisible();
    expect(strip.getByText("42%")).toBeVisible();
  });

  it("warns at the top when the candidates exceed the limit", async () => {
    renderLibrary({ summary: { candidateCount: 61 } });
    expect(
      await screen.findByText(
        "More than 60 skills are enabled, so Tapper can't offer all of them automatically. Some skills can only be selected manually.",
      ),
    ).toBeVisible();
  });

  it("shows no candidate notice at the limit", async () => {
    renderLibrary({ summary: { candidateCount: 60 } });
    await screen.findByText("BDD Scenario Design");
    await screen.findByText("14");
    expect(screen.queryByText(/can only be selected manually/)).toBeNull();
  });

  it("invites an import when the library is empty", async () => {
    const user = userEvent.setup();
    const { onImport, onCreate } = renderLibrary({
      items: [],
      summary: { skillTotal: 0, enabled: 0, usedInPeriod: 0, unused: 0 },
    });
    const empty = (
      await screen.findByText("Your skill library is empty")
    ).closest("div")!;
    await user.click(
      within(empty).getByRole("button", { name: "Import skill" }),
    );
    expect(onImport).toHaveBeenCalledTimes(1);
    await user.click(within(empty).getByRole("button", { name: "New skill" }));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("says so when filters match nothing", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderLibrary();
    await screen.findByText("BDD Scenario Design");
    fetchMock.mockImplementation((input: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            String(input).includes("/summary")
              ? envelope(SUMMARY)
              : envelope([]),
          ),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Status" }),
      "Disabled",
    );
    expect(
      await screen.findByText("No skills match these filters."),
    ).toBeVisible();
    expect(screen.queryByText("Your skill library is empty")).toBeNull();
  });

  it("keeps the cards when run data is unavailable", async () => {
    renderLibrary({ runtimeStatus: "unavailable" });
    expect(
      await screen.findByText("Run data is temporarily unavailable"),
    ).toBeVisible();
    expect(screen.getByText("BDD Scenario Design")).toBeVisible();
    expect(screen.getByText("Release Notes Writer")).toBeVisible();
  });

  it("links the CSV export to the selected period", async () => {
    renderLibrary();
    const link = await screen.findByRole("link", { name: "Export CSV" });
    const href = new URL(link.getAttribute("href")!, "http://localhost");
    expect(href.pathname).toBe(
      "/api/v1/projects/project-1/ai/skill-hub/export",
    );
    expect(href.searchParams.get("from")).toBe("2026-10-01");
    expect(href.searchParams.get("to")).toBe("2026-10-30");
  });
});

describe("SkillLibrary locale", () => {
  it("renders Chinese copy", async () => {
    renderLibrary({ summary: { candidateCount: 61 } }, "zh");
    const bdd = within(
      await screen.findByRole("listitem", { name: "BDD Scenario Design" }),
    );
    expect(bdd.getByText("测试设计")).toBeVisible();
    expect(bdd.getByText("导入")).toBeVisible();
    expect(bdd.getByText("已启用")).toBeVisible();
    expect(screen.getByRole("searchbox", { name: "搜索 Skill" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "类别" })).toBeVisible();
    expect(screen.getByRole("link", { name: "导出 CSV" })).toBeVisible();
    expect(
      await screen.findByText(
        "已启用的 Skill 超过 60 个，Tapper 无法全部自动选用，部分 Skill 只能手动选用。",
      ),
    ).toBeVisible();
    expect(screen.getByRole("region", { name: "库概览" })).toBeVisible();
  });
});
