import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import {
  createTestPlanClient,
  type TestPlanGenerationRequest,
  type TestPlanRevision,
} from "./client";

export const testPlanKeys = {
  all: (projectId: string) => ["test-plans", projectId] as const,
  generation: (projectId: string, jobId: string) =>
    ["test-plans", projectId, "generation", jobId] as const,
  revision: (projectId: string, planId: string, revisionId: string) =>
    ["test-plans", projectId, planId, revisionId] as const,
};

export function useTestPlans(projectId: string) {
  const client = useMemo(() => createTestPlanClient(projectId), [projectId]);
  return useQuery({
    queryKey: testPlanKeys.all(projectId),
    queryFn: ({ signal }) => client.list(signal),
  });
}

export function useTestPlanGeneration(projectId: string, jobId: string | null) {
  const client = useMemo(() => createTestPlanClient(projectId), [projectId]);
  return useQuery({
    queryKey: testPlanKeys.generation(projectId, jobId ?? ""),
    queryFn: ({ signal }) => client.generation(jobId!, signal),
    enabled: jobId !== null,
    refetchInterval: (query) =>
      query.state.data?.status === "DRAFT_READY" ||
      query.state.data?.status === "FAILED"
        ? false
        : 2000,
  });
}

export function useTestPlan(
  projectId: string,
  planId: string,
  revisionId: string,
) {
  const client = useMemo(() => createTestPlanClient(projectId), [projectId]);
  return useQuery({
    queryKey: testPlanKeys.revision(projectId, planId, revisionId),
    queryFn: ({ signal }) => client.get(planId, revisionId, signal),
    enabled: planId.length > 0 && revisionId.length > 0,
  });
}

export function useGenerateTestPlan(projectId: string) {
  const client = useMemo(() => createTestPlanClient(projectId), [projectId]);
  return useMutation({
    mutationFn: ({
      body,
      key,
    }: {
      body: TestPlanGenerationRequest;
      key: string;
    }) => client.generate(body, key),
  });
}

export function usePublishTestPlan(projectId: string) {
  const client = useMemo(() => createTestPlanClient(projectId), [projectId]);
  const cache = useQueryClient();
  return useMutation({
    mutationFn: ({ plan, key }: { plan: TestPlanRevision; key: string }) =>
      client.publish(plan, key),
    onSuccess: (plan) => {
      cache.setQueryData(
        testPlanKeys.revision(projectId, plan.testPlanId, plan.revisionId),
        plan,
      );
      void cache.invalidateQueries({ queryKey: testPlanKeys.all(projectId) });
    },
  });
}
