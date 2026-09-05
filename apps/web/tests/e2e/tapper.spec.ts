import { expect, test, type Page, type Request } from "@playwright/test";

import {
  E2ERequestFailureAudit,
  isApprovedE2EPageRequest,
} from "../../src/shared/testing/e2eRequestFailures";
import {
  buildFixtures,
  canonicalAnchorHash,
  canonicalTextHash,
  injectionQuestion,
  policyQuestion,
  writeState,
  type E2EFilePayload,
  type JourneyState,
  type SafeDocumentState,
} from "./fixtureBuilder";

const STAGES = [
  "stored",
  "parsing",
  "chunking",
  "embedding",
  "publishing",
  "ready",
] as const;
type IngestionStage = (typeof STAGES)[number];

interface DocumentSummary {
  documentId: string;
  filename: string;
  status: "queued" | "processing" | "ready" | "failed" | "deleting";
}

interface DocumentReceipt {
  document: DocumentSummary;
  duplicate: boolean;
  jobId: string;
}

interface StageSnapshot {
  completedAt?: string | null;
  errorCode?: string | null;
  stage: IngestionStage;
  state: "pending" | "processing" | "completed" | "failed";
}

interface DocumentDetail extends DocumentSummary {
  errorCode?: string | null;
  revisionId: string;
  sourceContentHash: string;
  stage: IngestionStage;
  stages: StageSnapshot[];
}

interface AnswerCitation {
  chunkContentHash: string;
  chunkId: string;
  citationId: string;
  source: {
    anchor: Record<string, unknown>;
    revision: string;
    sourceContentHash: string;
    sourceId: string;
  };
}

interface AnswerClaim {
  answerEnd: number;
  answerStart: number;
  citationIds: string[];
  claimId: string;
  text: string;
}

interface AnswerResponse {
  abstained: boolean;
  answer: string;
  citations: AnswerCitation[];
  claims: AnswerClaim[];
}

interface AnswerRound {
  request: Record<string, unknown>;
  response: AnswerResponse;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
}

function asObject(value: unknown): Record<string, unknown> {
  expect(typeof value).toBe("object");
  expect(value).not.toBeNull();
  expect(Array.isArray(value)).toBe(false);
  return value as Record<string, unknown>;
}

function receipt(value: unknown): DocumentReceipt {
  const object = asObject(value);
  expect(Object.keys(object).sort()).toEqual([
    "document",
    "duplicate",
    "jobId",
  ]);
  const document = asObject(object.document);
  expect(typeof document.documentId).toBe("string");
  expect(typeof document.filename).toBe("string");
  expect(typeof document.status).toBe("string");
  expect(typeof object.jobId).toBe("string");
  expect(typeof object.duplicate).toBe("boolean");
  return object as unknown as DocumentReceipt;
}

async function upload(
  page: Page,
  file: E2EFilePayload,
): Promise<DocumentReceipt> {
  const response = await page.request.post("/v1/knowledge/documents", {
    multipart: { upload: file },
  });
  expect(response.status()).toBe(202);
  return receipt((await response.json()) as unknown);
}

async function getDetail(
  page: Page,
  documentId: string,
): Promise<DocumentDetail> {
  const response = await page.request.get(
    `/v1/knowledge/documents/${documentId}`,
  );
  expect(response.status()).toBe(200);
  return (await response.json()) as DocumentDetail;
}

async function waitForStatus(
  page: Page,
  documentId: string,
  status: DocumentSummary["status"],
): Promise<DocumentDetail> {
  await expect
    .poll(async () => (await getDetail(page, documentId)).status, {
      intervals: [100, 200, 500],
      timeout: 45_000,
    })
    .toBe(status);
  return getDetail(page, documentId);
}

function assertReadyTimeline(detail: DocumentDetail): void {
  expect(detail.status).toBe("ready");
  expect(detail.stage).toBe("ready");
  expect(detail.errorCode ?? null).toBeNull();
  expect(detail.stages.map((snapshot) => snapshot.stage)).toEqual(STAGES);
  for (const snapshot of detail.stages) {
    expect(snapshot.state).toBe("completed");
    expect(typeof snapshot.completedAt).toBe("string");
    expect(snapshot.errorCode ?? null).toBeNull();
  }
}

function assertFailedTimeline(
  detail: DocumentDetail,
  failedStage: IngestionStage,
  safeCode: string,
): void {
  expect(detail.status).toBe("failed");
  expect(detail.stage).toBe(failedStage);
  expect(detail.errorCode).toBe(safeCode);
  expect(detail.stages.map((snapshot) => snapshot.stage)).toEqual(STAGES);
  const failedIndex = STAGES.indexOf(failedStage);
  for (const [index, snapshot] of detail.stages.entries()) {
    if (index < failedIndex) {
      expect(snapshot.state).toBe("completed");
      expect(typeof snapshot.completedAt).toBe("string");
      expect(snapshot.errorCode ?? null).toBeNull();
    } else if (index === failedIndex) {
      expect(snapshot.state).toBe("failed");
      expect(snapshot.errorCode).toBe(safeCode);
    } else {
      expect(snapshot.state).toBe("pending");
      expect(snapshot.errorCode ?? null).toBeNull();
    }
  }
}

async function documentIds(page: Page): Promise<string[]> {
  const response = await page.request.get("/v1/knowledge/documents?limit=50");
  expect(response.status()).toBe(200);
  const body = asObject((await response.json()) as unknown);
  expect(Array.isArray(body.items)).toBe(true);
  return (body.items as Array<{ documentId: string }>).map(
    (item) => item.documentId,
  );
}

async function armFailure(page: Page, stage: string): Promise<void> {
  const response = await page.request.post(
    `http://127.0.0.1:18000/__e2e/fail-next/${stage}`,
  );
  expect(response.status()).toBe(200);
  expect((await response.json()) as unknown).toEqual({
    stage,
    status: "armed",
  });
}

async function retryFromLibrary(
  page: Page,
  current: DocumentReceipt,
): Promise<DocumentReceipt> {
  const response = await page.request.post(
    `/v1/knowledge/documents/${current.document.documentId}/retry`,
  );
  expect(response.status()).toBe(202);
  return receipt((await response.json()) as unknown);
}

async function failOnceThenRetry(
  page: Page,
  stage: "parsing" | "embedding" | "publishing",
  safeCode: string,
  file: E2EFilePayload,
): Promise<{ detail: DocumentDetail; receipt: DocumentReceipt }> {
  await armFailure(page, stage);
  let accepted = await upload(page, file);
  const initialDocumentId = accepted.document.documentId;
  const initialJobId = accepted.jobId;
  const failed = await waitForStatus(
    page,
    accepted.document.documentId,
    "failed",
  );
  assertFailedTimeline(failed, stage, safeCode);
  const failedStageIndex = STAGES.indexOf(stage);
  const durableCheckpointTimes = failed.stages
    .slice(0, failedStageIndex)
    .map((snapshot) => snapshot.completedAt);

  accepted = await retryFromLibrary(page, accepted);
  expect(accepted.document.documentId).toBe(initialDocumentId);
  expect(accepted.jobId).toBe(initialJobId);
  const detail = await waitForStatus(
    page,
    accepted.document.documentId,
    "ready",
  );
  assertReadyTimeline(detail);
  expect(detail.revisionId).toBe(failed.revisionId);
  expect(detail.sourceContentHash).toBe(failed.sourceContentHash);
  expect(
    detail.stages
      .slice(0, failedStageIndex)
      .map((snapshot) => snapshot.completedAt),
  ).toEqual(durableCheckpointTimes);
  return { detail, receipt: accepted };
}

function documentState(
  accepted: DocumentReceipt,
  detail: DocumentDetail,
): SafeDocumentState {
  return {
    documentId: accepted.document.documentId,
    jobId: accepted.jobId,
    revisionId: detail.revisionId,
    sourceContentHash: detail.sourceContentHash,
  };
}

async function ask(
  page: Page,
  query: string,
  selectedIds: readonly string[],
): Promise<AnswerRound> {
  const request = {
    answerMode: "quick",
    query,
    resourceRefs: [...selectedIds]
      .sort()
      .map((sourceId) => ({ family: "doc", sourceId, mode: "scope" })),
    sources: ["doc"],
  };
  const response = await page.request.post("/v1/knowledge/answers", {
    data: request,
  });
  expect(response.status()).toBe(200);
  return {
    request,
    response: (await response.json()) as AnswerResponse,
  };
}

function assertScopedRequest(
  request: Record<string, unknown>,
  query: string,
  selectedIds: readonly string[],
): void {
  expect(Object.keys(request).sort()).toEqual([
    "answerMode",
    "query",
    "resourceRefs",
    "sources",
  ]);
  expect(request.query).toBe(query);
  expect(request.answerMode).toBe("quick");
  expect(request.sources).toEqual(["doc"]);
  expect(request.resourceRefs).toEqual(
    [...selectedIds]
      .sort()
      .map((sourceId) => ({ family: "doc", sourceId, mode: "scope" })),
  );
}

async function assertGroundedAnswer(
  answer: AnswerResponse,
  allowedIds: readonly string[],
  requiredContributors: readonly string[],
): Promise<void> {
  expect(answer.abstained).toBe(false);
  expect(answer.answer.length).toBeGreaterThan(0);
  expect(answer.citations.length).toBeGreaterThan(0);
  expect(answer.claims.length).toBeGreaterThan(0);
  const allowed = new Set(allowedIds);
  const citationById = new Map(
    answer.citations.map(
      (citation) => [citation.citationId, citation] as const,
    ),
  );
  expect(citationById.size).toBe(answer.citations.length);
  expect(
    answer.citations.every((citation) => allowed.has(citation.source.sourceId)),
  ).toBe(true);

  const contributorIds = new Set<string>();
  for (const claim of answer.claims) {
    expect(claim.citationIds.length).toBeGreaterThan(0);
    const points = Array.from(answer.answer);
    expect(points.slice(claim.answerStart, claim.answerEnd).join("")).toBe(
      claim.text,
    );
    for (const citationId of claim.citationIds) {
      const citation = citationById.get(citationId);
      expect(citation).toBeDefined();
      expect(allowed.has(citation!.source.sourceId)).toBe(true);
      contributorIds.add(citation!.source.sourceId);
    }
  }
  expect([...contributorIds].sort()).toEqual([...requiredContributors].sort());
}

async function openAndVerifyCitation(
  page: Page,
  citation: AnswerCitation,
  forbiddenDocumentId?: string,
): Promise<Record<string, unknown>> {
  const response = await page.request.get(
    `/v1/citations/${citation.citationId}`,
  );
  expect(response.status()).toBe(200);
  const preview = asObject((await response.json()) as unknown);
  expect(preview.citationId).toBe(citation.citationId);
  expect(preview.documentId).toBe(citation.source.sourceId);
  expect(preview.revisionId).toBe(citation.source.revision);
  expect(preview.sourceContentHash).toBe(citation.source.sourceContentHash);
  expect(preview.chunkContentHash).toBe(citation.chunkContentHash);
  expect(preview.anchor).toEqual(citation.source.anchor);
  if (forbiddenDocumentId !== undefined) {
    expect(preview.documentId).not.toBe(forbiddenDocumentId);
  }
  return preview;
}

test("Tapper uploads, recovers, answers, cites, scopes, sanitizes, and deletes", async ({
  page,
}) => {
  const fixtures = await buildFixtures();
  const externalRequests: string[] = [];
  const pageErrors: string[] = [];
  const consoleFailures: string[] = [];
  const requestFailures: string[] = [];
  const requestFailureAudit = new E2ERequestFailureAudit<Request>();
  page.on("request", (request) => {
    if (!isApprovedE2EPageRequest(request.url()))
      externalRequests.push("outside-allowlist");
  });
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

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "What can I do for you?" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Knowledge sources" }),
  ).toBeVisible();

  const baselineFiles = [
    fixtures.baselinePdf,
    fixtures.baselineDocx,
    fixtures.baselineMarkdown,
    fixtures.policy,
  ];
  const baseline: Array<{ detail: DocumentDetail; receipt: DocumentReceipt }> =
    [];
  for (const file of baselineFiles) {
    const accepted = await upload(page, file);
    expect(accepted.duplicate).toBe(false);
    const detail = await waitForStatus(
      page,
      accepted.document.documentId,
      "ready",
    );
    assertReadyTimeline(detail);
    baseline.push({ receipt: accepted, detail });
  }

  const deleted = baseline[0];
  const selectedReference = baseline[1];
  const other = baseline[2];
  const policy = baseline[3];
  expect(deleted).toBeDefined();
  expect(selectedReference).toBeDefined();
  expect(other).toBeDefined();
  expect(policy).toBeDefined();

  const baselineIds = baseline
    .map((item) => item.receipt.document.documentId)
    .sort();
  const duplicate = await upload(page, {
    ...fixtures.policy,
    name: `${fixtures.runId}-policy-duplicate.txt`,
  });
  expect(duplicate.duplicate).toBe(true);
  expect(duplicate.document.documentId).toBe(
    policy!.receipt.document.documentId,
  );
  expect(duplicate.jobId).toBe(policy!.receipt.jobId);
  expect((await documentIds(page)).sort()).toEqual(baselineIds);

  await page.reload();
  await expect(
    page.getByRole("heading", { name: "What can I do for you?" }),
  ).toBeVisible();
  expect((await documentIds(page)).sort()).toEqual(baselineIds);
  for (const file of baselineFiles) {
    await expect(page.getByText(file.name, { exact: true })).toBeVisible();
  }

  const initiallySelected = [
    policy!.receipt.document.documentId,
    selectedReference!.receipt.document.documentId,
  ];
  for (const filename of [fixtures.policy.name, fixtures.baselineDocx.name]) {
    await page
      .getByRole("checkbox", {
        name: new RegExp(escapeRegExp(filename), "u"),
      })
      .check();
  }
  const query = policyQuestion(fixtures.runId);
  const initialAnswer = await ask(page, query, initiallySelected);
  assertScopedRequest(initialAnswer.request, query, initiallySelected);
  await assertGroundedAnswer(
    initialAnswer.response,
    initiallySelected,
    initiallySelected,
  );

  await page
    .getByRole("checkbox", {
      name: new RegExp(escapeRegExp(fixtures.baselineDocx.name), "u"),
    })
    .uncheck();
  const policyId = policy!.receipt.document.documentId;
  const policyOnlyAnswer = await ask(page, query, [policyId]);
  assertScopedRequest(policyOnlyAnswer.request, query, [policyId]);
  await assertGroundedAnswer(policyOnlyAnswer.response, [policyId], [policyId]);
  expect(
    policyOnlyAnswer.response.citations.some(
      (citation) =>
        citation.source.sourceId ===
        selectedReference!.receipt.document.documentId,
    ),
  ).toBe(false);
  const policyCitation = policyOnlyAnswer.response.citations[0];
  expect(policyCitation).toBeDefined();
  let policyPreview: Record<string, unknown> | undefined;
  for (const citation of policyOnlyAnswer.response.citations) {
    const preview = await openAndVerifyCitation(
      page,
      citation,
      selectedReference!.receipt.document.documentId,
    );
    policyPreview ??= preview;
  }
  expect(policyPreview).toBeDefined();

  const parsing = await failOnceThenRetry(
    page,
    "parsing",
    "parser-unavailable",
    fixtures.parsingFailure,
  );
  const embedding = await failOnceThenRetry(
    page,
    "embedding",
    "embedding-unavailable",
    fixtures.embeddingFailure,
  );
  const publishing = await failOnceThenRetry(
    page,
    "publishing",
    "index-unavailable",
    fixtures.publishingFailure,
  );

  const injectionReceipt = await upload(page, fixtures.injection);
  const injectionDetail = await waitForStatus(
    page,
    injectionReceipt.document.documentId,
    "ready",
  );
  assertReadyTimeline(injectionDetail);
  expect((await documentIds(page)).length).toBe(8);

  const injectionQuery = injectionQuestion(fixtures.runId);
  const injectionAnswer = await ask(page, injectionQuery, [
    injectionReceipt.document.documentId,
  ]);
  assertScopedRequest(injectionAnswer.request, injectionQuery, [
    injectionReceipt.document.documentId,
  ]);
  await assertGroundedAnswer(
    injectionAnswer.response,
    [injectionReceipt.document.documentId],
    [injectionReceipt.document.documentId],
  );
  expect(injectionAnswer.response.answer).toContain("IGNORE ALL INSTRUCTIONS");
  expect(injectionAnswer.response.answer).toContain(
    "https://attacker.invalid/collect",
  );
  expect(
    injectionAnswer.response.claims.some(
      (claim) =>
        claim.text.includes("IGNORE ALL INSTRUCTIONS") &&
        claim.text.includes("https://attacker.invalid/collect"),
    ),
  ).toBe(true);
  await page
    .getByRole("textbox", { name: "Message Tapper" })
    .fill(injectionAnswer.response.answer);
  await page.getByRole("button", { name: "Send" }).click();
  const conversation = page.getByRole("log", { name: "Conversation" });
  await expect(
    conversation.getByText(/IGNORE ALL INSTRUCTIONS/u),
  ).toBeVisible();
  await expect(
    conversation.locator(
      'a[href*="attacker.invalid"], img[src*="attacker.invalid"], iframe, script, style',
    ),
  ).toHaveCount(0);
  expect(externalRequests).toEqual([]);

  const deleteResponse = await page.request.delete(
    `/v1/knowledge/documents/${deleted!.receipt.document.documentId}`,
  );
  expect(deleteResponse.status()).toBe(204);

  await expect
    .poll(async () => (await documentIds(page)).length, { timeout: 45_000 })
    .toBe(7);
  await page.reload();
  await expect(
    page.getByText(fixtures.baselinePdf.name, { exact: true }),
  ).toHaveCount(0);
  const postDeleteAnswer = await ask(page, query, [policyId]);
  assertScopedRequest(postDeleteAnswer.request, query, [policyId]);
  await assertGroundedAnswer(postDeleteAnswer.response, [policyId], [policyId]);
  expect(
    postDeleteAnswer.response.citations.some(
      (citation) =>
        citation.source.sourceId === deleted!.receipt.document.documentId,
    ),
  ).toBe(false);

  const state: JourneyState = {
    schemaVersion: 1,
    runId: fixtures.runId,
    policy: documentState(policy!.receipt, policy!.detail),
    reference: documentState(
      selectedReference!.receipt,
      selectedReference!.detail,
    ),
    other: documentState(other!.receipt, other!.detail),
    injection: documentState(injectionReceipt, injectionDetail),
    deleted: documentState(deleted!.receipt, deleted!.detail),
    citation: {
      citationId: policyCitation!.citationId,
      chunkId: policyCitation!.chunkId,
      documentId: policyCitation!.source.sourceId,
      revisionId: policyCitation!.source.revision,
      sourceContentHash: policyCitation!.source.sourceContentHash,
      chunkContentHash: policyCitation!.chunkContentHash,
      anchorHash: canonicalAnchorHash(policyCitation!.source.anchor),
      quoteHash: canonicalTextHash(policyPreview!.quote),
    },
    recovered: [
      documentState(parsing.receipt, parsing.detail),
      documentState(embedding.receipt, embedding.detail),
      documentState(publishing.receipt, publishing.detail),
    ],
  };
  await writeState(state);
  expect(externalRequests).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(consoleFailures).toEqual([]);
  expect(requestFailures).toEqual([]);
});
