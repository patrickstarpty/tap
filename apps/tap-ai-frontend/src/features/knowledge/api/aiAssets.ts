import type { components } from "../../../shared/api/generated/schema";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";

export type AiAgentRevision = components["schemas"]["AiAgentRevisionSummary"];
export type SkillRevision = components["schemas"]["SkillRevisionSummary"];

export function aiAssetPresentation(
  kind: "agent" | "skill",
  locale: "en" | "zh",
  capabilities: readonly string[],
) {
  const labels = capabilities.flatMap((capability) => {
    if (kind === "agent" && capability === "knowledge.search")
      return [locale === "zh" ? "检索知识来源" : "Searches sources"];
    if (kind === "agent" && capability === "knowledge.answer")
      return [locale === "zh" ? "回答知识问题" : "Answers questions"];
    if (kind === "skill" && capability === "knowledge.answer")
      return [locale === "zh" ? "用于知识回答" : "For knowledge answers"];
    return [];
  });
  const description =
    labels.length > 0
      ? labels.join(" · ")
      : locale === "zh"
        ? "已批准的能力"
        : "Approved capability";
  if (kind === "agent") {
    return {
      description,
      instructions:
        locale === "zh"
          ? "由服务端批准并锁定版本"
          : "Server-approved immutable revision",
    };
  }
  return {
    description,
    instructions:
      locale === "zh"
        ? "由服务端批准并锁定版本"
        : "Server-approved immutable revision",
  };
}

async function readCatalog<T>(
  projectId: string,
  kind: "agents" | "skills",
): Promise<T[]> {
  const response = await fetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/ai/${kind}`,
  );
  if (!response.ok) throw new Error("Approved AI catalog is unavailable.");
  const value = (await response.json()) as { items?: unknown };
  if (
    !value ||
    !Array.isArray(value.items) ||
    Object.keys(value).some((key) => key !== "items")
  )
    throw new Error("Approved AI catalog is unavailable.");
  return value.items as T[];
}

export function fetchAiAgentRevisions(projectId: string) {
  return readCatalog<AiAgentRevision>(projectId, "agents");
}

export function fetchSkillRevisions(projectId: string) {
  return readCatalog<SkillRevision>(projectId, "skills");
}

export function useAiAssetCatalog(projectId: string | null) {
  const agents = useQuery({
    queryKey: ["ai-agent-catalog", projectId],
    queryFn: () => fetchAiAgentRevisions(projectId!),
    enabled: projectId !== null,
    retry: false,
    staleTime: 60000,
  });
  const skills = useQuery({
    queryKey: ["skill-catalog", projectId],
    queryFn: () => fetchSkillRevisions(projectId!),
    enabled: projectId !== null,
    retry: false,
    staleTime: 60000,
  });
  return { agents, skills };
}

// -- Skills/Agents/Tools client hooks (Task 8) -----------------------------

export type SkillDraftRequest = components["schemas"]["SkillDraftRequest"];
export type SkillReviseRequest = components["schemas"]["SkillReviseRequest"];
export type SkillRevisionDetail = components["schemas"]["SkillRevisionDetail"];
export type SkillImportPreview = components["schemas"]["SkillImportPreview"];
export type SkillImportSelection =
  components["schemas"]["SkillImportSelection"];
export type SkillImportCommitResult =
  components["schemas"]["SkillImportCommitResult"];
export type AgentDraftRequest = components["schemas"]["AgentDraftRequest"];
export type AgentReviseRequest = components["schemas"]["AgentReviseRequest"];
export type AiAgentRevisionDetail =
  components["schemas"]["AiAgentRevisionDetail"];
export type ToolDescriptor = components["schemas"]["ToolDescriptor"];

/** Error shape every Skill/Agent route reports (`SkillProblem`); other
 * routes under `/ai` report RFC 9457 `ProblemDetails` instead, which this
 * normalizes to the same `{ code, detail }` shape so callers never branch
 * on response content type. */
export interface AiAssetProblem {
  code: string;
  detail: string;
}

export class AiAssetError extends Error implements AiAssetProblem {
  readonly code: string;
  readonly detail: string;

  constructor(problem: AiAssetProblem) {
    super(problem.detail);
    this.code = problem.code;
    this.detail = problem.detail;
  }
}

async function toProblem(response: Response): Promise<AiAssetProblem> {
  try {
    const body = (await response.json()) as Record<string, unknown>;
    if (typeof body.code === "string" && typeof body.detail === "string") {
      return { code: body.code, detail: body.detail };
    }
    if (typeof body.type === "string" && typeof body.detail === "string") {
      const code = body.type.split("/").pop();
      return {
        code: code && code.length > 0 ? code : "request_failed",
        detail: body.detail,
      };
    }
  } catch {
    // Body was not JSON (or had neither shape); fall through to the
    // generic problem below instead of surfacing a parse error.
  }
  return {
    code: "request_failed",
    detail: response.statusText || "The request failed.",
  };
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) throw new AiAssetError(await toProblem(response));
  return (await response.json()) as T;
}

function projectAiPath(projectId: string, suffix: string): string {
  return `/api/v1/projects/${encodeURIComponent(projectId)}/ai${suffix}`;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const aiAssetKeys = {
  all: (projectId: string) => ["ai-assets", projectId] as const,
  skills: (projectId: string) => ["ai-assets", projectId, "skills"] as const,
  skill: (projectId: string, revisionId: string) =>
    ["ai-assets", projectId, "skill", revisionId] as const,
  skillRevisions: (projectId: string, skillId: string) =>
    ["ai-assets", projectId, "skill-revisions", skillId] as const,
  agents: (projectId: string) => ["ai-assets", projectId, "agents"] as const,
  agent: (projectId: string, revisionId: string) =>
    ["ai-assets", projectId, "agent", revisionId] as const,
  agentRevisions: (projectId: string, agentId: string) =>
    ["ai-assets", projectId, "agent-revisions", agentId] as const,
  tools: (projectId: string, locale: "en" | "zh" | null) =>
    ["ai-assets", projectId, "tools", locale] as const,
};

function invalidateAiAssets(queryClient: QueryClient, projectId: string) {
  return queryClient.invalidateQueries({
    queryKey: aiAssetKeys.all(projectId),
  });
}

// -- Skill reads -------------------------------------------------------

export function useSkills(projectId: string) {
  return useQuery({
    queryKey: aiAssetKeys.skills(projectId),
    queryFn: () =>
      requestJson<components["schemas"]["SkillRevisionPage"]>(
        projectAiPath(projectId, "/skills"),
      ).then((page) => page.items),
    staleTime: 30_000,
  });
}

export function useSkill(projectId: string, revisionId: string | null) {
  return useQuery({
    queryKey: aiAssetKeys.skill(projectId, revisionId ?? "none"),
    queryFn: () =>
      requestJson<SkillRevisionDetail>(
        projectAiPath(
          projectId,
          `/skills/${encodeURIComponent(revisionId ?? "")}`,
        ),
      ),
    enabled: revisionId !== null,
    staleTime: 30_000,
  });
}

export function useSkillRevisions(projectId: string, skillId: string | null) {
  return useQuery({
    queryKey: aiAssetKeys.skillRevisions(projectId, skillId ?? "none"),
    queryFn: () =>
      requestJson<components["schemas"]["SkillRevisionPage"]>(
        projectAiPath(
          projectId,
          `/skills/${encodeURIComponent(skillId ?? "")}/revisions`,
        ),
      ).then((page) => page.items),
    enabled: skillId !== null,
    staleTime: 30_000,
  });
}

export function skillFileUrl(
  projectId: string,
  revisionId: string,
  path: string,
): string {
  const encodedPath = path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return projectAiPath(
    projectId,
    `/skills/${encodeURIComponent(revisionId)}/files/${encodedPath}`,
  );
}

// -- Skill writes --------------------------------------------------------

export type SkillImportInput =
  | { archive: File }
  | { files: { file: File; relativePath: string }[] }
  | { text: string };

function buildSkillImportInit(input: SkillImportInput): RequestInit {
  if ("text" in input) return jsonInit("POST", { text: input.text });
  const form = new FormData();
  if ("archive" in input) {
    form.append("archive", input.archive, input.archive.name);
  } else {
    for (const { file, relativePath } of input.files) {
      form.append("files", file, relativePath);
    }
  }
  return { method: "POST", body: form };
}

export function useStageSkillImport(projectId: string) {
  return useMutation({
    mutationKey: ["ai-assets", projectId, "skill-import-stage"],
    mutationFn: (input: SkillImportInput) =>
      requestJson<SkillImportPreview>(
        projectAiPath(projectId, "/skills/imports"),
        buildSkillImportInit(input),
      ),
  });
}

export function useCommitSkillImport(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["ai-assets", projectId, "skill-import-commit"],
    mutationFn: ({
      importId,
      selections,
    }: {
      importId: string;
      selections: SkillImportSelection[];
    }) =>
      requestJson<SkillImportCommitResult>(
        projectAiPath(
          projectId,
          `/skills/imports/${encodeURIComponent(importId)}/commit`,
        ),
        jsonInit("POST", { selections }),
      ),
    onSuccess: () => invalidateAiAssets(queryClient, projectId),
  });
}

export function useCreateSkill(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["ai-assets", projectId, "skill-create"],
    mutationFn: (draft: SkillDraftRequest) =>
      requestJson<SkillRevisionDetail>(
        projectAiPath(projectId, "/skills"),
        jsonInit("POST", draft),
      ),
    onSuccess: () => invalidateAiAssets(queryClient, projectId),
  });
}

export function useReviseSkill(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["ai-assets", projectId, "skill-revise"],
    mutationFn: ({
      skillId,
      request,
    }: {
      skillId: string;
      request: SkillReviseRequest;
    }) =>
      requestJson<SkillRevisionDetail>(
        projectAiPath(
          projectId,
          `/skills/${encodeURIComponent(skillId)}/revisions`,
        ),
        jsonInit("POST", request),
      ),
    onSuccess: () => invalidateAiAssets(queryClient, projectId),
  });
}

export function useSetSkillEnabled(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["ai-assets", projectId, "skill-enabled"],
    mutationFn: ({ skillId, enabled }: { skillId: string; enabled: boolean }) =>
      requestJson<SkillRevision>(
        projectAiPath(
          projectId,
          `/skills/${encodeURIComponent(skillId)}/${enabled ? "enable" : "disable"}`,
        ),
        { method: "POST" },
      ),
    onSuccess: () => invalidateAiAssets(queryClient, projectId),
  });
}

// -- Agent reads ----------------------------------------------------------

export function useAgents(projectId: string) {
  return useQuery({
    queryKey: aiAssetKeys.agents(projectId),
    queryFn: () =>
      requestJson<components["schemas"]["AiAgentRevisionPage"]>(
        projectAiPath(projectId, "/agents"),
      ).then((page) => page.items),
    staleTime: 30_000,
  });
}

export function useAgent(projectId: string, revisionId: string | null) {
  return useQuery({
    queryKey: aiAssetKeys.agent(projectId, revisionId ?? "none"),
    queryFn: () =>
      requestJson<AiAgentRevision>(
        projectAiPath(
          projectId,
          `/agents/${encodeURIComponent(revisionId ?? "")}`,
        ),
      ),
    enabled: revisionId !== null,
    staleTime: 30_000,
  });
}

export function useAgentRevisions(projectId: string, agentId: string | null) {
  return useQuery({
    queryKey: aiAssetKeys.agentRevisions(projectId, agentId ?? "none"),
    queryFn: () =>
      requestJson<components["schemas"]["AiAgentRevisionPage"]>(
        projectAiPath(
          projectId,
          `/agents/${encodeURIComponent(agentId ?? "")}/revisions`,
        ),
      ).then((page) => page.items),
    enabled: agentId !== null,
    staleTime: 30_000,
  });
}

// -- Agent writes --------------------------------------------------------

export function useCreateAgent(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["ai-assets", projectId, "agent-create"],
    mutationFn: (draft: AgentDraftRequest) =>
      requestJson<AiAgentRevisionDetail>(
        projectAiPath(projectId, "/agents"),
        jsonInit("POST", draft),
      ),
    onSuccess: () => invalidateAiAssets(queryClient, projectId),
  });
}

export function useReviseAgent(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["ai-assets", projectId, "agent-revise"],
    mutationFn: ({
      agentId,
      request,
    }: {
      agentId: string;
      request: AgentReviseRequest;
    }) =>
      requestJson<AiAgentRevisionDetail>(
        projectAiPath(
          projectId,
          `/agents/${encodeURIComponent(agentId)}/revisions`,
        ),
        jsonInit("POST", request),
      ),
    onSuccess: () => invalidateAiAssets(queryClient, projectId),
  });
}

export function useSetAgentEnabled(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["ai-assets", projectId, "agent-enabled"],
    mutationFn: ({ agentId, enabled }: { agentId: string; enabled: boolean }) =>
      requestJson<AiAgentRevision>(
        projectAiPath(
          projectId,
          `/agents/${encodeURIComponent(agentId)}/${enabled ? "enable" : "disable"}`,
        ),
        { method: "POST" },
      ),
    onSuccess: () => invalidateAiAssets(queryClient, projectId),
  });
}

// -- Tools ------------------------------------------------------------------

export function useTools(projectId: string, locale: "en" | "zh" | null = null) {
  return useQuery({
    queryKey: aiAssetKeys.tools(projectId, locale),
    queryFn: () =>
      requestJson<components["schemas"]["ToolPage"]>(
        projectAiPath(
          projectId,
          `/tools${locale !== null ? `?locale=${locale}` : ""}`,
        ),
      ).then((page) => page.items),
    staleTime: 30_000,
  });
}
