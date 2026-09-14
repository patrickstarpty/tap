import type { components } from "../../../shared/api/generated/schema";
import { useQuery } from "@tanstack/react-query";

export type AiAgentRevision = components["schemas"]["AiAgentRevisionSummary"];
export type SkillRevision = components["schemas"]["SkillRevisionSummary"];

export function aiAssetPresentation(
  kind: "agent" | "skill",
  locale: "en" | "zh",
) {
  if (kind === "agent") {
    return {
      description:
        locale === "zh"
          ? "受控知识问答智能体"
          : "Governed Knowledge answer agent",
      instructions:
        locale === "zh"
          ? "由服务端批准并锁定版本"
          : "Server-approved immutable revision",
    };
  }
  return {
    description:
      locale === "zh" ? "为知识回答生成可追溯引用" : "Adds traceable citations",
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
