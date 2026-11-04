import type { components } from "../../../shared/api/generated/schema";
import { useQuery } from "@tanstack/react-query";

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

export type AgentRevisionView = components["schemas"]["AiAgentRevisionView"];
export type SkillRevisionView = components["schemas"]["SkillRevisionView"];
export type ToolView = components["schemas"]["ToolView"];
export type McpServerView = components["schemas"]["McpServerView"];
export type AgentDraftRequest = components["schemas"]["AgentDraftRequest"];
export type McpServerCreateRequest =
  components["schemas"]["McpServerCreateRequest"];

export class CatalogError extends Error {
  constructor(public readonly code: string) {
    super(code);
  }
}
export async function catalogRequest<T>(
  projectId: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/ai/${path}`,
    init,
  );
  if (!response.ok) {
    const problem = (await response.json().catch(() => ({}))) as {
      code?: string;
      type?: string;
    };
    throw new CatalogError(
      problem.code ?? problem.type?.split("/").at(-1) ?? "catalog-unavailable",
    );
  }
  return response.json() as Promise<T>;
}
function jsonBody(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}
export const managementCatalog = {
  agents: (projectId: string) =>
    catalogRequest<{ items: AgentRevisionView[] }>(
      projectId,
      "agents/revisions",
    ),
  skills: (projectId: string) =>
    catalogRequest<{ items: SkillRevisionView[] }>(
      projectId,
      "skills/revisions",
    ),
  tools: (projectId: string) =>
    catalogRequest<{ items: ToolView[] }>(projectId, "tools"),
  servers: (projectId: string) =>
    catalogRequest<{ items: McpServerView[] }>(projectId, "mcp-servers"),
  saveAgent: (projectId: string, body: AgentDraftRequest, assetId?: string) =>
    catalogRequest<AgentRevisionView>(
      projectId,
      assetId ? `agents/${encodeURIComponent(assetId)}/revisions` : "agents",
      jsonBody("POST", body),
    ),
  saveSkill: (
    projectId: string,
    body: components["schemas"]["SkillCreateRequest"],
  ) =>
    catalogRequest<components["schemas"]["SkillImportResult"]>(
      projectId,
      "skills",
      jsonBody("POST", body),
    ),
  importSkill: (projectId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return catalogRequest<components["schemas"]["SkillImportResult"]>(
      projectId,
      "skills/imports",
      { method: "POST", body },
    );
  },
  status: (
    projectId: string,
    kind: "agent" | "skill",
    revisionId: string,
    status: "enabled" | "disabled",
  ) =>
    catalogRequest(
      projectId,
      `${kind}s/revisions/${encodeURIComponent(revisionId)}/status`,
      jsonBody("POST", { status }),
    ),
  registerServer: (projectId: string, body: McpServerCreateRequest) =>
    catalogRequest<McpServerView>(
      projectId,
      "mcp-servers",
      jsonBody("POST", body),
    ),
  syncServer: (projectId: string, serverId: string) =>
    catalogRequest<McpServerView>(
      projectId,
      `mcp-servers/${encodeURIComponent(serverId)}/sync`,
      { method: "POST" },
    ),
  serverStatus: (projectId: string, serverId: string, enabled: boolean) =>
    catalogRequest<McpServerView>(
      projectId,
      `mcp-servers/${encodeURIComponent(serverId)}`,
      jsonBody("PATCH", { enabled }),
    ),
};
export async function fetchSkillContent(
  projectId: string,
  revisionId: string,
): Promise<string> {
  const response = await fetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/ai/skills/${encodeURIComponent(revisionId)}/content`,
  );
  if (!response.ok) throw new CatalogError("skill-content-unavailable");
  return response.text();
}
export async function fetchSkillDownload(
  projectId: string,
  revisionId: string,
): Promise<Blob> {
  const response = await fetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/ai/skills/${encodeURIComponent(revisionId)}/content`,
  );
  if (!response.ok) throw new CatalogError("skill-content-unavailable");
  return response.blob();
}
export function catalogErrorMessage(error: unknown, zh: boolean): string {
  const code =
    error instanceof CatalogError ? error.code : "catalog-unavailable";
  const messages: Record<string, [string, string]> = {
    "mcp-url-not-allowed": [
      "This MCP URL is not allowed. Use a local server or ask to allow its host.",
      "此 MCP 地址未获允许，请使用本机服务或配置允许的主机。",
    ],
    "mcp-sync-failed": [
      "Could not sync tools. Check the server and try again.",
      "工具同步失败，请检查服务后重试。",
    ],
    "mcp-auth-env-unset": [
      "The authentication environment variable is missing on the server.",
      "服务端未设置该鉴权环境变量。",
    ],
    "skill-invalid": [
      "Check the SKILL.md name, description and instructions.",
      "请检查 SKILL.md 名称、描述和指令。",
    ],
  };
  return (
    messages[code]?.[zh ? 1 : 0] ??
    (zh
      ? `操作失败，请重试（${code}）。`
      : `Could not complete this action. Retry (${code}).`)
  );
}
