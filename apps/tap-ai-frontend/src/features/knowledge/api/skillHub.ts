import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import type { components } from "../../../shared/api/generated/schema";
import type { TeamPeriod } from "./agentTeam";
import {
  aiAssetKeys,
  jsonInit,
  projectAiPath,
  requestJson,
  skillHubRootKey,
} from "./aiAssets";

type Schemas = components["schemas"];

export type SkillHubCatalogView = Schemas["SkillHubCatalogView"];
export type SkillHubCatalogItem = Schemas["SkillHubCatalogItem"];
export type SkillHubSkill = Schemas["SkillHubSkill"];
export type SkillHubSummaryView = Schemas["SkillHubSummaryView"];
export type SkillHubLibrarySummary = Schemas["SkillHubLibrarySummary"];
export type SkillHubDetailView = Schemas["SkillHubDetailView"];
export type SkillHubDetail = Schemas["SkillHubDetail"];
export type SkillHubUsageView = Schemas["SkillHubUsageView"];
export type SkillCategory = SkillHubSkill["category"];
export type SkillSourceKind = SkillHubSkill["sourceKind"];
export type SkillTask = NonNullable<
  Schemas["SkillReviseRequest"]["applicableTasks"]
>[number];

export interface CatalogFilters {
  q?: string;
  category?: SkillCategory;
  task?: SkillTask;
  source?: SkillSourceKind;
  status?: SkillHubSkill["status"];
  sort?: "name" | "last_used_at" | "activations" | "updated_at";
  order?: "asc" | "desc";
}

/** Drops unset filters so `{ q: "" }` and `{}` (same URL) share one cache
 * entry and one request. */
function definedFilters(filters: CatalogFilters): CatalogFilters {
  return Object.fromEntries(
    Object.entries(filters).filter(
      ([, value]) => value !== null && value !== undefined && value !== "",
    ),
  ) as CatalogFilters;
}

export const skillHubKeys = {
  all: skillHubRootKey,
  catalog: (projectId: string, period: TeamPeriod, filters: CatalogFilters) =>
    [
      "skill-hub",
      projectId,
      "catalog",
      period.from,
      period.to,
      definedFilters(filters),
    ] as const,
  summary: (projectId: string, period: TeamPeriod) =>
    ["skill-hub", projectId, "summary", period.from, period.to] as const,
  detail: (projectId: string, skillId: string, period: TeamPeriod) =>
    [
      "skill-hub",
      projectId,
      "detail",
      skillId,
      period.from,
      period.to,
    ] as const,
  usage: (projectId: string, skillId: string, period: TeamPeriod) =>
    ["skill-hub", projectId, "usage", skillId, period.from, period.to] as const,
};

function query(params: Record<string, string | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "")
      search.set(key, value);
  }
  const text = search.toString();
  return text === "" ? "" : `?${text}`;
}

function periodQuery(period: TeamPeriod): Record<string, string> {
  return { from: period.from, to: period.to };
}

const skillPath = (skillId: string) =>
  `/skill-hub/catalog/${encodeURIComponent(skillId)}`;

const STALE_MS = 30_000;

export function useSkillHubCatalog(
  projectId: string,
  period: TeamPeriod,
  filters: CatalogFilters = {},
) {
  return useQuery({
    queryKey: skillHubKeys.catalog(projectId, period, filters),
    placeholderData: keepPreviousData,
    queryFn: () =>
      requestJson<SkillHubCatalogView>(
        projectAiPath(
          projectId,
          `/skill-hub/catalog${query({
            ...periodQuery(period),
            q: filters.q?.trim(),
            category: filters.category,
            task: filters.task,
            source: filters.source,
            status: filters.status,
            sort: filters.sort,
            order: filters.order,
          })}`,
        ),
      ),
    staleTime: STALE_MS,
  });
}

export function useSkillHubSummary(projectId: string, period: TeamPeriod) {
  return useQuery({
    queryKey: skillHubKeys.summary(projectId, period),
    placeholderData: keepPreviousData,
    queryFn: () =>
      requestJson<SkillHubSummaryView>(
        projectAiPath(
          projectId,
          `/skill-hub/summary${query(periodQuery(period))}`,
        ),
      ),
    staleTime: STALE_MS,
  });
}

export function useSkillHubDetail(
  projectId: string,
  skillId: string | null,
  period: TeamPeriod,
) {
  return useQuery({
    queryKey: skillHubKeys.detail(projectId, skillId ?? "none", period),
    queryFn: () =>
      requestJson<SkillHubDetailView>(
        projectAiPath(
          projectId,
          `${skillPath(skillId ?? "")}${query(periodQuery(period))}`,
        ),
      ),
    enabled: skillId !== null,
    staleTime: STALE_MS,
  });
}

export function useSkillHubUsage(
  projectId: string,
  skillId: string | null,
  period: TeamPeriod,
) {
  return useQuery({
    queryKey: skillHubKeys.usage(projectId, skillId ?? "none", period),
    queryFn: () =>
      requestJson<SkillHubUsageView>(
        projectAiPath(
          projectId,
          `${skillPath(skillId ?? "")}/usage${query(periodQuery(period))}`,
        ),
      ),
    enabled: skillId !== null,
    staleTime: STALE_MS,
  });
}

export function skillHubExportUrl(
  projectId: string,
  period: TeamPeriod,
): string {
  return projectAiPath(
    projectId,
    `/skill-hub/export${query(periodQuery(period))}`,
  );
}

// -- Writes. Every write refetches the library and the Skill catalogs.

function useSkillHubWrite<TInput>(
  projectId: string,
  name: string,
  run: (input: TInput) => Promise<SkillHubSkill>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["skill-hub", projectId, name],
    mutationFn: run,
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: skillHubKeys.all(projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: aiAssetKeys.all(projectId),
        }),
      ]),
  });
}

export function useUpdateSkillProfile(projectId: string) {
  return useSkillHubWrite(
    projectId,
    "profile",
    ({ skillId, category }: { skillId: string; category: SkillCategory }) =>
      requestJson<SkillHubSkill>(
        projectAiPath(
          projectId,
          `/skills/${encodeURIComponent(skillId)}/profile`,
        ),
        jsonInit("PUT", { category }),
      ),
  );
}

export function useTransferSkillOwner(projectId: string) {
  return useSkillHubWrite(
    projectId,
    "transfer-owner",
    ({
      skillId,
      newOwnerActorId,
    }: {
      skillId: string;
      newOwnerActorId: string;
    }) =>
      requestJson<SkillHubSkill>(
        projectAiPath(
          projectId,
          `/skills/${encodeURIComponent(skillId)}/owner`,
        ),
        jsonInit("POST", { newOwnerActorId }),
      ),
  );
}
