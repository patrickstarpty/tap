import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import type { components } from "../../../shared/api/generated/schema";
import { aiAssetKeys, jsonInit, projectAiPath, requestJson } from "./aiAssets";

type Schemas = components["schemas"];

export type AgentTeamSummaryView = Schemas["AgentTeamSummaryView"];
export type AgentTeamMembersView = Schemas["AgentTeamMembersView"];
export type AgentTeamMemberView = Schemas["AgentTeamMemberView"];
export type AgentTeamMemberRow = Schemas["AgentTeamMemberRow"];
export type AgentTeamPeopleView = Schemas["AgentTeamPeopleView"];
export type AgentTeamPersonView = Schemas["AgentTeamPersonView"];
export type AgentTeamOrgChartView = Schemas["AgentTeamOrgChartView"];
export type AgentTeamReportView = Schemas["AgentTeamReportView"];
export type AgentTeamHistoryPage = Schemas["AgentTeamHistoryPage"];
export type AgentTeamHistoryEvent = Schemas["AgentTeamHistoryEvent"];

export type PeriodPreset =
  "last7" | "last30" | "thisMonth" | "lastMonth" | "custom";

export interface TeamPeriod {
  from: string;
  to: string;
}

export interface MemberFilters {
  owner?: string | null;
  status?: "active" | "idle" | "disabled" | null;
  kind?: "custom" | "built_in" | "tapper_default" | null;
  sort?: string;
  order?: "asc" | "desc";
}

function isoDate(value: Date): string {
  return value.toISOString().slice(0, 10);
}

/** Inclusive UTC date range for a preset; the server always works in UTC. */
export function resolvePeriod(
  preset: PeriodPreset,
  today: Date,
  custom?: TeamPeriod,
): TeamPeriod {
  const year = today.getUTCFullYear();
  const month = today.getUTCMonth();
  const day = (offset: number) =>
    new Date(Date.UTC(year, month, today.getUTCDate() + offset));
  switch (preset) {
    case "last7":
      return { from: isoDate(day(-6)), to: isoDate(day(0)) };
    case "last30":
      return { from: isoDate(day(-29)), to: isoDate(day(0)) };
    case "thisMonth":
      return {
        from: isoDate(new Date(Date.UTC(year, month, 1))),
        to: isoDate(day(0)),
      };
    case "lastMonth":
      return {
        from: isoDate(new Date(Date.UTC(year, month - 1, 1))),
        to: isoDate(new Date(Date.UTC(year, month, 0))),
      };
    case "custom":
      return custom ?? { from: isoDate(day(-6)), to: isoDate(day(0)) };
  }
}

export const agentTeamKeys = {
  all: (projectId: string) => ["agent-team", projectId] as const,
  summary: (projectId: string, period: TeamPeriod) =>
    ["agent-team", projectId, "summary", period.from, period.to] as const,
  members: (projectId: string, period: TeamPeriod, filters: MemberFilters) =>
    [
      "agent-team",
      projectId,
      "members",
      period.from,
      period.to,
      filters,
    ] as const,
  member: (projectId: string, agentKey: string, period: TeamPeriod) =>
    [
      "agent-team",
      projectId,
      "member",
      agentKey,
      period.from,
      period.to,
    ] as const,
  history: (projectId: string, agentKey: string) =>
    ["agent-team", projectId, "history", agentKey] as const,
  people: (projectId: string, period: TeamPeriod) =>
    ["agent-team", projectId, "people", period.from, period.to] as const,
  person: (projectId: string, actorId: string, period: TeamPeriod) =>
    [
      "agent-team",
      projectId,
      "person",
      actorId,
      period.from,
      period.to,
    ] as const,
  orgChart: (projectId: string, period: TeamPeriod) =>
    ["agent-team", projectId, "org-chart", period.from, period.to] as const,
  report: (projectId: string, period: TeamPeriod) =>
    ["agent-team", projectId, "report", period.from, period.to] as const,
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

const STALE_MS = 30_000;

export function useTeamSummary(projectId: string, period: TeamPeriod) {
  return useQuery({
    queryKey: agentTeamKeys.summary(projectId, period),
    queryFn: () =>
      requestJson<AgentTeamSummaryView>(
        projectAiPath(
          projectId,
          `/agent-team/summary${query(periodQuery(period))}`,
        ),
      ),
    staleTime: STALE_MS,
  });
}

export function useTeamMembers(
  projectId: string,
  period: TeamPeriod,
  filters: MemberFilters = {},
) {
  return useQuery({
    queryKey: agentTeamKeys.members(projectId, period, filters),
    queryFn: () =>
      requestJson<AgentTeamMembersView>(
        projectAiPath(
          projectId,
          `/agent-team/members${query({
            ...periodQuery(period),
            owner: filters.owner,
            status: filters.status,
            kind: filters.kind,
            sort: filters.sort,
            order: filters.order,
          })}`,
        ),
      ),
    staleTime: STALE_MS,
  });
}

export function useTeamMember(
  projectId: string,
  agentKey: string | null,
  period: TeamPeriod,
) {
  return useQuery({
    queryKey: agentTeamKeys.member(projectId, agentKey ?? "none", period),
    queryFn: () =>
      requestJson<AgentTeamMemberView>(
        projectAiPath(
          projectId,
          `/agent-team/members/${encodeURIComponent(agentKey ?? "")}${query(periodQuery(period))}`,
        ),
      ),
    enabled: agentKey !== null,
    staleTime: STALE_MS,
  });
}

export function useMemberHistory(projectId: string, agentId: string | null) {
  return useInfiniteQuery({
    queryKey: agentTeamKeys.history(projectId, agentId ?? "none"),
    queryFn: ({ pageParam }) =>
      requestJson<AgentTeamHistoryPage>(
        projectAiPath(
          projectId,
          `/agent-team/members/${encodeURIComponent(agentId ?? "")}/history${query({ cursor: pageParam })}`,
        ),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    enabled: agentId !== null,
    staleTime: STALE_MS,
  });
}

export function useTeamPeople(projectId: string, period: TeamPeriod) {
  return useQuery({
    queryKey: agentTeamKeys.people(projectId, period),
    queryFn: () =>
      requestJson<AgentTeamPeopleView>(
        projectAiPath(
          projectId,
          `/agent-team/people${query(periodQuery(period))}`,
        ),
      ),
    staleTime: STALE_MS,
  });
}

export function usePerson(
  projectId: string,
  actorId: string | null,
  period: TeamPeriod,
) {
  return useQuery({
    queryKey: agentTeamKeys.person(projectId, actorId ?? "none", period),
    queryFn: () =>
      requestJson<AgentTeamPersonView>(
        projectAiPath(
          projectId,
          `/agent-team/people/${encodeURIComponent(actorId ?? "")}${query(periodQuery(period))}`,
        ),
      ),
    enabled: actorId !== null,
    staleTime: STALE_MS,
  });
}

export function useOrgChart(projectId: string, period: TeamPeriod) {
  return useQuery({
    queryKey: agentTeamKeys.orgChart(projectId, period),
    queryFn: () =>
      requestJson<AgentTeamOrgChartView>(
        projectAiPath(
          projectId,
          `/agent-team/org-chart${query(periodQuery(period))}`,
        ),
      ),
    staleTime: STALE_MS,
  });
}

export function useTeamReport(projectId: string, period: TeamPeriod) {
  return useQuery({
    queryKey: agentTeamKeys.report(projectId, period),
    queryFn: () =>
      requestJson<AgentTeamReportView>(
        projectAiPath(
          projectId,
          `/agent-team/report${query(periodQuery(period))}`,
        ),
      ),
    staleTime: STALE_MS,
  });
}

export function agentTeamExportUrl(
  projectId: string,
  period: TeamPeriod,
): string {
  return projectAiPath(
    projectId,
    `/agent-team/export${query(periodQuery(period))}`,
  );
}

// -- Writes. Responses carry no runtime status, so callers never render
// runtime numbers from them; every write refetches the team views instead.

function useTeamWrite<TInput, TResult>(
  projectId: string,
  name: string,
  run: (input: TInput) => Promise<TResult>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationKey: ["agent-team", projectId, name],
    mutationFn: run,
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({
          queryKey: agentTeamKeys.all(projectId),
        }),
        queryClient.invalidateQueries({
          queryKey: aiAssetKeys.all(projectId),
        }),
      ]),
  });
}

export function useUpdateJobTitle(projectId: string) {
  return useTeamWrite(
    projectId,
    "job-title",
    ({ agentId, jobTitle }: { agentId: string; jobTitle: string | null }) =>
      requestJson<unknown>(
        projectAiPath(
          projectId,
          `/agents/${encodeURIComponent(agentId)}/profile`,
        ),
        jsonInit("PUT", { jobTitle }),
      ),
  );
}

export function useTransferOwner(projectId: string) {
  return useTeamWrite(
    projectId,
    "transfer-owner",
    ({
      agentId,
      newOwnerActorId,
    }: {
      agentId: string;
      newOwnerActorId: string;
    }) =>
      requestJson<unknown>(
        projectAiPath(
          projectId,
          `/agents/${encodeURIComponent(agentId)}/owner`,
        ),
        jsonInit("POST", { newOwnerActorId }),
      ),
  );
}

export function useResolveFeedback(projectId: string) {
  return useTeamWrite(
    projectId,
    "resolve-feedback",
    ({ feedbackId, note }: { feedbackId: string; note?: string | null }) =>
      requestJson<unknown>(
        projectAiPath(
          projectId,
          `/feedback/${encodeURIComponent(feedbackId)}/resolve`,
        ),
        jsonInit("POST", note ? { note } : {}),
      ),
  );
}
