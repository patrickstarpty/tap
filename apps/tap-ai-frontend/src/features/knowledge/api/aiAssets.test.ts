import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "../../../shared/testing/renderApp";
import {
  aiAssetKeys,
  aiAssetPresentation,
  useAgent,
  useAgentRevisions,
  useAgents,
  useCommitSkillImport,
  useCreateAgent,
  useCreateSkill,
  useReviseAgent,
  useReviseSkill,
  useSetAgentEnabled,
  useSetSkillEnabled,
  useSkill,
  useSkillRevisions,
  useSkills,
  useStageSkillImport,
  useTools,
  skillFileUrl,
} from "./aiAssets";

function wrapperFor(queryClient: ReturnType<typeof createTestQueryClient>) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("approved AI asset presentation", () => {
  it("uses user-facing meaning instead of an integrity digest", () => {
    expect(
      aiAssetPresentation("agent", "zh", [
        "knowledge.search",
        "knowledge.answer",
      ]),
    ).toEqual({
      description: "检索知识来源 · 回答知识问题",
      instructions: "由服务端批准并锁定版本",
    });
    expect(
      JSON.stringify(aiAssetPresentation("skill", "en", ["knowledge.answer"])),
    ).not.toContain("sha256:");
  });
});

describe("skillFileUrl", () => {
  it("builds the download path and encodes each relative-path segment", () => {
    expect(skillFileUrl("project/a", "rev_1", "references/guide.md")).toBe(
      "/api/v1/projects/project%2Fa/ai/skills/rev_1/files/references/guide.md",
    );
    expect(skillFileUrl("project-1", "rev_1", "a b/c#.md")).toBe(
      "/api/v1/projects/project-1/ai/skills/rev_1/files/a%20b/c%23.md",
    );
  });
});

describe("Skill read hooks", () => {
  it("requests the Skill catalog and unwraps the items envelope", async () => {
    const skill = {
      revisionId: "rev_1",
      skillId: "skill_1",
      assetId: "skill_1",
      displayName: "Policy lookup",
      contentDigest: "digest",
      applicableTasks: ["knowledge.answer"],
      sourceKind: "authored",
      builtIn: false,
      status: "enabled",
      isLatest: true,
    };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json({ items: [skill] }));
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useSkills("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await waitFor(() => expect(result.current.data).toEqual([skill]));
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/ai/skills",
      undefined,
    );
  });

  it("fetches a single Skill revision only once a revisionId is supplied", async () => {
    const detail = {
      revisionId: "rev_1",
      skillId: "skill_1",
      assetId: "skill_1",
      displayName: "Policy lookup",
      contentDigest: "digest",
      applicableTasks: ["knowledge.answer"],
      sourceKind: "authored",
      builtIn: false,
      status: "enabled",
      isLatest: true,
    };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(detail));
    const queryClient = createTestQueryClient();
    const { result, rerender } = renderHook(
      ({ revisionId }: { revisionId: string | null }) =>
        useSkill("project-1", revisionId),
      {
        wrapper: wrapperFor(queryClient),
        initialProps: { revisionId: null as string | null },
      },
    );
    expect(result.current.fetchStatus).toBe("idle");
    expect(fetcher).not.toHaveBeenCalled();

    rerender({ revisionId: "rev_1" });
    await waitFor(() => expect(result.current.data).toEqual(detail));
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/ai/skills/rev_1",
      undefined,
    );
  });

  it("lists a Skill's revision history", async () => {
    const revision = {
      revisionId: "rev_2",
      skillId: "skill_1",
      assetId: "skill_1",
      displayName: "Policy lookup",
      contentDigest: "digest-2",
      applicableTasks: ["knowledge.answer"],
      sourceKind: "authored",
      builtIn: false,
      status: "enabled",
      isLatest: true,
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ items: [revision] }),
    );
    const queryClient = createTestQueryClient();
    const { result } = renderHook(
      () => useSkillRevisions("project-1", "skill_1"),
      { wrapper: wrapperFor(queryClient) },
    );
    await waitFor(() => expect(result.current.data).toEqual([revision]));
  });
});

describe("Skill write hooks", () => {
  it("stages a multipart archive import without invalidating the catalog", async () => {
    const preview = {
      importId: "import_1",
      expiresAt: "2026-10-18T00:00:00Z",
      skills: [],
    };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(preview, { status: 201 }));
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useStageSkillImport("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    const archive = new File(["zip-bytes"], "skills.zip");
    let data: unknown;
    await act(async () => {
      data = await result.current.mutateAsync({ archive });
    });
    expect(data).toEqual(preview);
    const [path, init] = fetcher.mock.calls[0]!;
    expect(path).toBe("/api/v1/projects/project-1/ai/skills/imports");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
    const sentArchive = (init!.body as FormData).get("archive") as File;
    expect(sentArchive.name).toBe(archive.name);
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("stages relative-path files and a JSON text import", async () => {
    const preview = {
      importId: "import_2",
      expiresAt: "2026-10-18T00:00:00Z",
      skills: [],
    };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => Response.json(preview, { status: 201 }));
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useStageSkillImport("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    const file = new File(["skill body"], "SKILL.md");
    await act(async () => {
      await result.current.mutateAsync({
        files: [{ file, relativePath: "my-skill/SKILL.md" }],
      });
    });
    const filesInit = fetcher.mock.calls[0]![1]!;
    const sentFile = (filesInit.body as FormData).get("files") as File;
    expect(sentFile.name).toBe("my-skill/SKILL.md");
    expect(filesInit.method).toBe("POST");

    await act(async () => {
      await result.current.mutateAsync({ text: "---\nname: a\n---\nBody" });
    });
    const textInit = fetcher.mock.calls[1]![1]!;
    expect(textInit.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(textInit.body as string)).toEqual({
      text: "---\nname: a\n---\nBody",
    });
  });

  it("commits a staged import and invalidates the project's ai-assets queries", async () => {
    const commitResult = { revisions: [] };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(commitResult));
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCommitSkillImport("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await act(async () => {
      await result.current.mutateAsync({
        importId: "import_1",
        selections: [{ root: "my-skill" }],
      });
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/ai/skills/imports/import_1/commit",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ selections: [{ root: "my-skill" }] }),
      }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: aiAssetKeys.all("project-1"),
    });
  });

  it("creates a Skill and invalidates the project's ai-assets queries", async () => {
    const created = { revisionId: "rev_1", skillId: "skill_1" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(created, { status: 201 }),
    );
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCreateSkill("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    let data: unknown;
    await act(async () => {
      data = await result.current.mutateAsync({
        name: "Policy lookup",
        description: "Looks up policy clauses.",
        instructions: "Answer using the attached references.",
      });
    });
    expect(data).toEqual(created);
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: aiAssetKeys.all("project-1"),
    });
    // Same composer-cache concern as the Agent case above.
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["skill-catalog", "project-1"],
    });
  });

  it("revises a Skill against a base revision", async () => {
    const revised = { revisionId: "rev_2", skillId: "skill_1" };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(revised, { status: 201 }));
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useReviseSkill("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await act(async () => {
      await result.current.mutateAsync({
        skillId: "skill_1",
        request: {
          baseRevisionId: "rev_1",
          name: "Policy lookup",
          description: "Looks up policy clauses.",
          instructions: "Answer using the attached references.",
        },
      });
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/ai/skills/skill_1/revisions",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("enables and disables a Skill", async () => {
    const summary = { revisionId: "rev_1", skillId: "skill_1" };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => Response.json(summary));
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useSetSkillEnabled("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await act(async () => {
      await result.current.mutateAsync({ skillId: "skill_1", enabled: false });
    });
    expect(fetcher).toHaveBeenLastCalledWith(
      "/api/v1/projects/project-1/ai/skills/skill_1/disable",
      { method: "POST" },
    );
    await act(async () => {
      await result.current.mutateAsync({ skillId: "skill_1", enabled: true });
    });
    expect(fetcher).toHaveBeenLastCalledWith(
      "/api/v1/projects/project-1/ai/skills/skill_1/enable",
      { method: "POST" },
    );
  });

  it("converts a SkillProblem error body into { code, detail }", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        {
          code: "skill_name_conflict",
          detail: "A skill with this name exists.",
        },
        { status: 409 },
      ),
    );
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useCreateSkill("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await act(async () => {
      await expect(
        result.current.mutateAsync({
          name: "dup",
          description: "d",
          instructions: "i",
        }),
      ).rejects.toMatchObject({
        code: "skill_name_conflict",
        detail: "A skill with this name exists.",
      });
    });
  });

  it("converts an RFC 9457 ProblemDetails body into { code, detail }", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json(
        {
          type: "https://tap.example/problems/authorization-denied",
          title: "Forbidden",
          detail: "ai.skills.write is required.",
          status: 403,
          retryable: false,
          correlationId: "corr-1",
        },
        { status: 403 },
      ),
    );
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useCreateSkill("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await act(async () => {
      await expect(
        result.current.mutateAsync({
          name: "a",
          description: "d",
          instructions: "i",
        }),
      ).rejects.toMatchObject({
        code: "authorization-denied",
        detail: "ai.skills.write is required.",
      });
    });
  });
});

describe("Agent read hooks", () => {
  it("requests the Agent catalog and unwraps the items envelope", async () => {
    const agent = {
      revisionId: "rev_1",
      agentId: "agent_1",
      assetId: "agent_1",
      displayName: "Triage agent",
      contentDigest: "digest",
      toolAllowlist: ["search"],
      outputSchemaDigest: "schema-digest",
      sourceKind: "authored",
      builtIn: false,
      status: "enabled",
      isLatest: true,
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ items: [agent] }),
    );
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useAgents("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await waitFor(() => expect(result.current.data).toEqual([agent]));
  });

  it("fetches a single Agent revision only once a revisionId is supplied", async () => {
    const agent = {
      revisionId: "rev_1",
      agentId: "agent_1",
      assetId: "agent_1",
      displayName: "Triage agent",
      contentDigest: "digest",
      toolAllowlist: ["search"],
      outputSchemaDigest: "schema-digest",
      sourceKind: "authored",
      builtIn: false,
      status: "enabled",
      isLatest: true,
    };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(agent));
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useAgent("project-1", "rev_1"), {
      wrapper: wrapperFor(queryClient),
    });
    await waitFor(() => expect(result.current.data).toEqual(agent));
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/ai/agents/rev_1",
      undefined,
    );
  });

  it("lists an Agent's revision history", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ items: [] }),
    );
    const queryClient = createTestQueryClient();
    const { result } = renderHook(
      () => useAgentRevisions("project-1", "agent_1"),
      { wrapper: wrapperFor(queryClient) },
    );
    await waitFor(() => expect(result.current.data).toEqual([]));
  });
});

describe("Agent write hooks", () => {
  it("creates an Agent and invalidates the project's ai-assets queries", async () => {
    const created = { revisionId: "rev_1", agentId: "agent_1" };
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json(created, { status: 201 }));
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const { result } = renderHook(() => useCreateAgent("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await act(async () => {
      await result.current.mutateAsync({
        name: "Triage agent",
        description: "Classifies incoming requests.",
        systemInstruction: "Classify the request.",
      });
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/ai/agents",
      expect.objectContaining({ method: "POST" }),
    );
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: aiAssetKeys.all("project-1"),
    });
    // The composer reads the catalog through the older
    // `useAiAssetCatalog` query keys (not `aiAssetKeys`), so a just-created
    // Agent must invalidate those too, or it can't be selected/prefilled
    // from the composer until that separate 60s cache happens to expire.
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ["ai-agent-catalog", "project-1"],
    });
  });

  it("revises an Agent against a base revision", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        Response.json({ revisionId: "rev_2" }, { status: 201 }),
      );
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useReviseAgent("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await act(async () => {
      await result.current.mutateAsync({
        agentId: "agent_1",
        request: {
          baseRevisionId: "rev_1",
          name: "Triage agent",
          description: "Classifies incoming requests.",
          systemInstruction: "Classify the request.",
        },
      });
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/ai/agents/agent_1/revisions",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("enables and disables an Agent", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json({ revisionId: "rev_1" }));
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useSetAgentEnabled("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await act(async () => {
      await result.current.mutateAsync({ agentId: "agent_1", enabled: false });
    });
    expect(fetcher).toHaveBeenLastCalledWith(
      "/api/v1/projects/project-1/ai/agents/agent_1/disable",
      { method: "POST" },
    );
  });
});

describe("agent-team refresh after writes", () => {
  async function expectTeamInvalidated(
    run: (
      queryClient: ReturnType<typeof createTestQueryClient>,
    ) => Promise<void>,
  ) {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      Response.json({ revisionId: "rev_1", agentId: "agent_1" }),
    );
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(["agent-team", "project-1", "members"], {});
    await run(queryClient);
    expect(
      queryClient.getQueryState(["agent-team", "project-1", "members"])
        ?.isInvalidated,
    ).toBe(true);
  }

  it("invalidates the team roster after create, revise and enable", async () => {
    await expectTeamInvalidated(async (queryClient) => {
      const { result } = renderHook(() => useCreateAgent("project-1"), {
        wrapper: wrapperFor(queryClient),
      });
      await act(async () => {
        await result.current.mutateAsync({
          name: "A",
          description: "d",
          systemInstruction: "s",
        });
      });
    });
    await expectTeamInvalidated(async (queryClient) => {
      const { result } = renderHook(() => useReviseAgent("project-1"), {
        wrapper: wrapperFor(queryClient),
      });
      await act(async () => {
        await result.current.mutateAsync({
          agentId: "agent_1",
          request: {
            baseRevisionId: "rev_1",
            name: "A",
            description: "d",
            systemInstruction: "s",
          },
        });
      });
    });
    await expectTeamInvalidated(async (queryClient) => {
      const { result } = renderHook(() => useSetAgentEnabled("project-1"), {
        wrapper: wrapperFor(queryClient),
      });
      await act(async () => {
        await result.current.mutateAsync({
          agentId: "agent_1",
          enabled: false,
        });
      });
    });
    await expectTeamInvalidated(async (queryClient) => {
      const { result } = renderHook(() => useSetSkillEnabled("project-1"), {
        wrapper: wrapperFor(queryClient),
      });
      await act(async () => {
        await result.current.mutateAsync({
          skillId: "skill_1",
          enabled: false,
        });
      });
    });
  });
});

describe("useTools", () => {
  it("requests the Tool catalog without a locale by default", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json({ items: [] }));
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useTools("project-1"), {
      wrapper: wrapperFor(queryClient),
    });
    await waitFor(() => expect(result.current.data).toEqual([]));
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/ai/tools",
      undefined,
    );
  });

  it("passes the locale query parameter when supplied", async () => {
    const fetcher = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(Response.json({ items: [] }));
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useTools("project-1", "zh"), {
      wrapper: wrapperFor(queryClient),
    });
    await waitFor(() => expect(result.current.data).toEqual([]));
    expect(fetcher).toHaveBeenCalledWith(
      "/api/v1/projects/project-1/ai/tools?locale=zh",
      undefined,
    );
  });
});
