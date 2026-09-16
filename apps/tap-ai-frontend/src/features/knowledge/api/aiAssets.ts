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
