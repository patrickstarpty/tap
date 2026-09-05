import { expect, test, type Page, type Request } from "@playwright/test";

import {
  E2ERequestFailureAudit,
  isApprovedE2EPageRequest,
} from "../../src/shared/testing/e2eRequestFailures";
import {
  canonicalAnchorHash,
  canonicalTextHash,
  policyQuestion,
  readState,
  type SafeDocumentState,
} from "./fixtureBuilder";

interface AnswerResponse {
  abstained: boolean;
  claims: Array<{ citationIds: string[]; text: string }>;
  citations: Array<{ source: { sourceId: string } }>;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

async function assertCurrentDocument(
  page: Page,
  expected: SafeDocumentState,
): Promise<void> {
  const response = await page.request.get(
    `/v1/knowledge/documents/${expected.documentId}`,
  );
  expect(response.status()).toBe(200);
  const detail = (await response.json()) as Record<string, unknown>;
  expect(detail.status).toBe("ready");
  expect(detail.revisionId).toBe(expected.revisionId);
  expect(detail.sourceContentHash).toBe(expected.sourceContentHash);
}

test("Tapper durable state survives the selected restart boundary", async ({
  page,
}) => {
  const phase = process.env.TAPPER_E2E_PHASE;
  expect(["app-restart", "compose-restart"]).toContain(phase);
  const state = await readState();
  const survivors = [
    state.policy,
    state.reference,
    state.other,
    state.injection,
    ...state.recovered,
  ];

  const listResponse = await page.request.get(
    "/v1/knowledge/documents?limit=50",
  );
  expect(listResponse.status()).toBe(200);
  const list = (await listResponse.json()) as {
    items: Array<{ documentId: string; filename: string; status: string }>;
  };
  expect(list.items.map((item) => item.documentId).sort()).toEqual(
    survivors.map((item) => item.documentId).sort(),
  );
  expect(
    list.items.some((item) => item.documentId === state.deleted.documentId),
  ).toBe(false);
  for (const document of survivors) await assertCurrentDocument(page, document);

  const citationResponse = await page.request.get(
    `/v1/citations/${state.citation.citationId}`,
  );
  expect(citationResponse.status()).toBe(200);
  const preview = (await citationResponse.json()) as Record<string, unknown>;
  expect(preview.citationId).toBe(state.citation.citationId);
  expect(preview.documentId).toBe(state.citation.documentId);
  expect(preview.revisionId).toBe(state.citation.revisionId);
  expect(preview.sourceContentHash).toBe(state.citation.sourceContentHash);
  expect(preview.chunkContentHash).toBe(state.citation.chunkContentHash);
  expect(canonicalAnchorHash(preview.anchor)).toBe(state.citation.anchorHash);
  expect(canonicalTextHash(preview.quote)).toBe(state.citation.quoteHash);

  const pageErrors: string[] = [];
  const consoleFailures: string[] = [];
  const requestFailures: string[] = [];
  const requestFailureAudit = new E2ERequestFailureAudit<Request>();
  const externalRequests: string[] = [];
  page.on("pageerror", () => pageErrors.push("pageerror"));
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      consoleFailures.push(message.type());
    }
  });
  page.on("response", (response) => {
    const request = response.request();
    requestFailureAudit.observeResponse(request, {
      method: request.method(),
      status: response.status(),
      url: request.url(),
    });
  });
  page.on("requestfailed", (request) => {
    const failure = requestFailureAudit.unexpectedFailure(request, {
      errorText: request.failure()?.errorText ?? "",
      method: request.method(),
      url: request.url(),
    });
    if (failure !== null) requestFailures.push(failure);
  });
  page.on("request", (request) => {
    if (!isApprovedE2EPageRequest(request.url()))
      externalRequests.push("outside-allowlist");
  });

  await page.goto("/tests/e2e/tapper-harness.html");
  await page.getByRole("tab", { name: "知识库" }).click();
  for (const survivor of survivors) {
    const listed = list.items.find(
      (item) => item.documentId === survivor.documentId,
    );
    expect(listed).toBeDefined();
    await expect(
      page.getByRole("row").filter({ hasText: listed!.filename }),
    ).toBeVisible();
  }
  await expect(page.getByText("已就绪 7", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "问答" }).click();
  await page
    .getByRole("checkbox", {
      name: new RegExp(escapeRegExp(state.policy.documentId), "u"),
    })
    .check();
  await page
    .getByRole("textbox", { name: "输入问题" })
    .fill(policyQuestion(state.runId));
  const pending = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/v1/knowledge/answers",
  );
  await page.getByRole("button", { name: "提问" }).click();
  const answerHttp = await pending;
  expect(answerHttp.status()).toBe(200);
  const answer = (await answerHttp.json()) as AnswerResponse;
  expect(answer.abstained).toBe(false);
  expect(answer.citations.length).toBeGreaterThan(0);
  expect(answer.claims.length).toBeGreaterThan(0);
  expect(
    answer.citations.every(
      (citation) => citation.source.sourceId === state.policy.documentId,
    ),
  ).toBe(true);
  expect(
    answer.citations.some(
      (citation) => citation.source.sourceId === state.deleted.documentId,
    ),
  ).toBe(false);
  const renderedClaims = page.locator(".tapper-grounded-claim");
  await expect(renderedClaims).toHaveCount(answer.claims.length);
  for (const [index, claim] of answer.claims.entries()) {
    const rendered = renderedClaims.nth(index);
    await expect(rendered).toContainText(claim.text);
    expect(claim.citationIds.length).toBeGreaterThan(0);
    await expect(
      rendered.getByRole("button", { name: /^引用 \d+$/u }).first(),
    ).toBeVisible();
  }
  const persistedFactClaims = renderedClaims.filter({
    hasText: new RegExp(
      `Tapper ${escapeRegExp(state.runId)} refund requests`,
      "u",
    ),
  });
  await expect(persistedFactClaims).toHaveCount(1);
  await expect(persistedFactClaims).toBeVisible();
  expect(externalRequests).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(consoleFailures).toEqual([]);
  expect(requestFailures).toEqual([]);
});
