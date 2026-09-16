import createOpenApiClient from "openapi-fetch";

import type { components, paths } from "../../../shared/api/generated/schema";

export type TestPlanRevision = components["schemas"]["TestPlanRevisionView"];
export type TestPlanGenerationRequest =
  components["schemas"]["TestPlanGenerationRequestBody"];
export type TestPlanGeneration =
  components["schemas"]["TestPlanGenerationAccepted"];

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
  publish(plan: TestPlanRevision, key: string): Promise<TestPlanRevision>;
}

export function createTestPlanClient(
  projectId: string,
  baseUrl = "",
): TestPlanClient {
  if (!projectId.trim())
    throw new Error("A project ID is required for Test Plans.");
  const http = createOpenApiClient<paths>({ baseUrl });
  const pathProject = { project_id: projectId };
  const required = <T>(data: T | undefined, message: string): T => {
    if (data === undefined) throw new Error(message);
    return data;
  };
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
      return required(result.data, "Test Plan publish failed.");
    },
  };
}
