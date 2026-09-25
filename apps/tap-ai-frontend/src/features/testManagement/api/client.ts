import createOpenApiClient from "openapi-fetch";

import type { components, paths } from "../../../shared/api/generated/schema";

export type TestPlanRevision = components["schemas"]["TestPlanRevisionView"];
export type TestPlanGenerationRequest =
  components["schemas"]["TestPlanGenerationRequestBody"];
export type TestPlanGeneration =
  components["schemas"]["TestPlanGenerationAccepted"];
export type TestPlanReviewDisposition =
  components["schemas"]["TestPlanReviewRequest"]["disposition"];

export class TestPlanApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly problemType: string | null,
  ) {
    super(message);
  }
}

export interface TestPlanClient {
  list(signal?: AbortSignal): Promise<TestPlanRevision[]>;
  get(
    planId: string,
    revisionId: string,
    signal?: AbortSignal,
  ): Promise<TestPlanRevision>;
  generate(
    body: TestPlanGenerationRequest,
    key: string,
  ): Promise<TestPlanGeneration>;
  generation(jobId: string, signal?: AbortSignal): Promise<TestPlanGeneration>;
  retry(
    jobId: string,
    rowVersion: number,
    key: string,
  ): Promise<TestPlanGeneration>;
  update(plan: TestPlanRevision, key: string): Promise<TestPlanRevision>;
  review(
    plan: TestPlanRevision,
    disposition: TestPlanReviewDisposition,
    reason: string,
    key: string,
  ): Promise<TestPlanRevision>;
  publish(plan: TestPlanRevision, key: string): Promise<TestPlanRevision>;
  fork(plan: TestPlanRevision, key: string): Promise<TestPlanRevision>;
}

export function createTestPlanClient(
  projectId: string,
  baseUrl = "",
): TestPlanClient {
  if (!projectId.trim())
    throw new Error("A project ID is required for Test Plans.");
  const http = createOpenApiClient<paths>({ baseUrl });
  const pathProject = { project_id: projectId };
  const required = <T>(
    data: T | undefined,
    message: string,
    response?: Response,
    error?: unknown,
  ): T => {
    if (data === undefined) {
      const problem = error as { type?: string; detail?: string } | undefined;
      throw new TestPlanApiError(
        problem?.detail ?? message,
        response?.status ?? 0,
        problem?.type ?? null,
      );
    }
    return data;
  };
  const updateBody = (plan: TestPlanRevision) => ({
    title: plan.title,
    objective: plan.objective,
    scopeItems: plan.scopeItems,
    prerequisites: plan.prerequisites,
    risks: plan.risks,
    cases: plan.cases,
    citations: plan.citations,
    assumptions: plan.assumptions,
    unknowns: plan.unknowns,
    coverageGaps: plan.coverageGaps,
  });
  return {
    async list(signal) {
      const result = await http.GET(
        "/api/v1/projects/{project_id}/test-plans",
        {
          params: { path: pathProject },
          signal,
        },
      );
      return required(result.data, "Test Plans are unavailable.").items;
    },
    async get(planId, revisionId, signal) {
      const result = await http.GET(
        "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}",
        {
          params: {
            path: {
              ...pathProject,
              test_plan_id: planId,
              revision_id: revisionId,
            },
          },
          signal,
        },
      );
      return required(result.data, "Test Plan is unavailable.");
    },
    async generate(body, key) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/test-plans/generations",
        {
          params: { path: pathProject, header: { "idempotency-key": key } },
          body,
        },
      );
      return required(result.data, "Test Plan generation failed.");
    },
    async generation(jobId, signal) {
      const result = await http.GET(
        "/api/v1/projects/{project_id}/test-plans/generations/{job_id}",
        { params: { path: { ...pathProject, job_id: jobId } }, signal },
      );
      return required(
        result.data,
        "Test Plan generation status is unavailable.",
        result.response,
        result.error,
      );
    },
    async retry(jobId, rowVersion, key) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/test-plans/generations/{job_id}/retry",
        {
          params: {
            path: { ...pathProject, job_id: jobId },
            header: { "If-Match": rowVersion, "idempotency-key": key },
          },
        },
      );
      return required(
        result.data,
        "Test Plan generation retry failed.",
        result.response,
        result.error,
      );
    },
    async update(plan, key) {
      const result = await http.PATCH(
        "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}",
        {
          params: {
            path: {
              ...pathProject,
              test_plan_id: plan.testPlanId,
              revision_id: plan.revisionId,
            },
            header: {
              "If-Match": plan.rowVersion,
              "idempotency-key": key,
            },
          },
          body: updateBody(plan),
        },
      );
      return required(
        result.data,
        "Test Plan update failed.",
        result.response,
        result.error,
      );
    },
    async review(plan, disposition, reason, key) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}/reviews",
        {
          params: {
            path: {
              ...pathProject,
              test_plan_id: plan.testPlanId,
              revision_id: plan.revisionId,
            },
            header: {
              "If-Match": plan.rowVersion,
              "idempotency-key": key,
            },
          },
          body: { disposition, reason },
        },
      );
      return required(
        result.data,
        "Test Plan review failed.",
        result.response,
        result.error,
      );
    },
    async publish(plan, key) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}/publish",
        {
          params: {
            path: {
              ...pathProject,
              test_plan_id: plan.testPlanId,
              revision_id: plan.revisionId,
            },
            header: { "If-Match": plan.rowVersion, "idempotency-key": key },
          },
        },
      );
      return required(
        result.data,
        "Test Plan publish failed.",
        result.response,
        result.error,
      );
    },
    async fork(plan, key) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/test-plans/{test_plan_id}/revisions/{revision_id}/fork",
        {
          params: {
            path: {
              ...pathProject,
              test_plan_id: plan.testPlanId,
              revision_id: plan.revisionId,
            },
            header: {
              "If-Match": plan.rowVersion,
              "idempotency-key": key,
            },
          },
        },
      );
      return required(
        result.data,
        "Test Plan revision could not be created.",
        result.response,
        result.error,
      );
    },
  };
}
