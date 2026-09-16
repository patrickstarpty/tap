import { expect, test, type Page, type Request } from "@playwright/test";

import {
  E2ERequestFailureAudit,
  isApprovedE2EPageRequest,
} from "../../src/shared/testing/e2eRequestFailures";
import {
  canonicalAnchorHash,
  canonicalTextHash,
  policyQuestion,
  readConversationState,
  readState,
  type SafeDocumentState,
} from "./fixtureBuilder";

interface AnswerResponse {
  abstained: boolean;
  claims: Array<{ citationIds: string[]; text: string }>;
  citations: Array<{ source: { sourceId: string } }>;
}

const ORIGIN = "http://127.0.0.1:15173";

async function assertCurrentDocument(
  page: Page,
  expected: SafeDocumentState,
  knowledgePath: string,
): Promise<void> {
  const response = await page.request.get(
    `${knowledgePath}/documents/${expected.documentId}`,
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
  const runtimeHttp = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeHttp.status()).toBe(200);
  const runtime = (await runtimeHttp.json()) as { projectId: string };
  expect(typeof runtime.projectId).toBe("string");
  expect(runtime.projectId.trim()).not.toBe("");
  const knowledgePath = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}/knowledge`;
  const survivors = [
    state.policy,
    state.reference,
    state.other,
    state.injection,
    ...state.recovered,
  ];

  const listResponse = await page.request.get(
    `${knowledgePath}/documents?limit=50`,
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
  const policyFilename = list.items.find(
    (item) => item.documentId === state.policy.documentId,
  )?.filename;
  expect(policyFilename).toBeDefined();
  for (const document of survivors)
    await assertCurrentDocument(page, document, knowledgePath);

  const graphSnapshots = await page.request.get(
    `${knowledgePath}/graph/snapshots`,
    { params: { sourceRevisionId: state.policy.revisionId } },
  );
  expect(graphSnapshots.status()).toBe(200);
  const graphSnapshot = (await graphSnapshots.json()) as {
    items: Array<{ snapshotId: string; status: string }>;
  };
  expect(graphSnapshot.items[0]?.status).toBe("READY");
  const graphQuery = await page.request.post(`${knowledgePath}/graph/query`, {
    headers: { Origin: ORIGIN },
    data: {
      snapshotId: graphSnapshot.items[0]!.snapshotId,
      query: "*",
      nodeLimit: 500,
    },
  });
  expect(graphQuery.status()).toBe(200);
  const persistedGraph = (await graphQuery.json()) as {
    nodes: Array<{ nodeId: string }>;
    evidence: Array<{ evidenceId: string }>;
  };
  expect(persistedGraph.nodes.length).toBeGreaterThan(0);
  expect(persistedGraph.evidence.length).toBeGreaterThan(0);

  const citationResponse = await page.request.get(
    `${knowledgePath}/citations/${state.citation.citationId}`,
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
  const requestFailureAudit = new E2ERequestFailureAudit<Request>(
    runtime.projectId,
  );
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

  await page.goto("/");
  const conversationState = await readConversationState();
  const modelsResponse = await page.request.get(
    `/api/v1/projects/${encodeURIComponent(runtime.projectId)}/ai/models`,
  );
  expect(modelsResponse.status()).toBe(200);
  const models = (await modelsResponse.json()) as {
    items: Array<{ alias: string; displayName: string }>;
  };
  const selectedModel = models.items.find(
    (model) => model.alias === conversationState.modelAlias,
  );
  expect(selectedModel).toBeDefined();
  const history = page.getByRole("navigation", { name: "Chat history" });
  await history
    .getByRole("button", { name: new RegExp(conversationState.prompt, "u") })
    .click();
  const transcript = page.getByRole("log", { name: "Conversation" });
  await expect(
    transcript.getByText(conversationState.prompt, { exact: true }),
  ).toBeVisible();
  const persistedTurn = transcript
    .locator(".tap-turn")
    .filter({ hasText: conversationState.prompt })
    .first();
  const messageContext = persistedTurn.locator(".tap-message-context");
  await messageContext
    .getByText(/Message context|本轮上下文/u, { exact: false })
    .click();
  await expect(
    messageContext.getByText(conversationState.sourceLabel, { exact: true }),
  ).toBeVisible();
  const answerContext = persistedTurn.locator(".tap-answer-context");
  await answerContext
    .getByText(
      /Sources and settings used for this answer|本次回答使用的资料与配置/u,
    )
    .click();
  for (const label of [
    conversationState.agentLabel,
    conversationState.skillLabel,
  ]) {
    await expect(
      answerContext.getByText(new RegExp(label, "u"), { exact: false }),
    ).toBeVisible();
  }
  await expect(
    page.getByRole("button", {
      name: `Select model, current model ${selectedModel!.displayName}`,
    }),
  ).toBeVisible();
  await transcript
    .getByRole("button", {
      name: /Open source citation 1|打开来源引用 1/u,
    })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: /Cited source|原文依据/u }),
  ).toBeVisible();
  await expect(
    page
      .getByLabel(/Source text|原文/u)
      .getByText("verified identity evidence", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Close source text|关闭原文/u })
    .click();

  await page
    .getByRole("checkbox", { name: new RegExp(policyFilename!, "u") })
    .check();
  const futureRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname ===
        `/api/v1/projects/${encodeURIComponent(runtime.projectId)}/conversations/${conversationState.conversationId}/turns`,
  );
  const futureResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `/api/v1/projects/${encodeURIComponent(runtime.projectId)}/conversations/${conversationState.conversationId}/turns`,
  );
  await page
    .getByRole("textbox", { name: "Message Tapper" })
    .fill(`Future context ${phase}`);
  await page.getByRole("button", { name: "Send" }).click();
  const futureInput = (await futureRequest).postDataJSON() as {
    agentRevisionId: string | null;
    skillRevisionIds: string[];
    sourceRevisionIds: string[];
  };
  expect(futureInput.sourceRevisionIds).toEqual([state.policy.revisionId]);
  expect(futureInput.agentRevisionId).toBeNull();
  expect(futureInput.skillRevisionIds).toEqual([]);
  const acceptedResponse = await futureResponse;
  expect(acceptedResponse.status()).toBe(202);
  const accepted = (await acceptedResponse.json()) as { turnId: string };
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `/api/v1/projects/${encodeURIComponent(runtime.projectId)}/conversations/${conversationState.conversationId}`,
        );
        const body = (await response.json()) as {
          turns: Array<{ state: string; turnId: string }>;
        };
        return body.turns.find((turn) => turn.turnId === accepted.turnId)
          ?.state;
      },
      { timeout: 45_000 },
    )
    .toMatch(/completed|abstained|failed/u);

  await page.getByRole("button", { name: "Library", exact: true }).click();
  for (const survivor of survivors) {
    const listed = list.items.find(
      (item) => item.documentId === survivor.documentId,
    );
    expect(listed).toBeDefined();
    await expect(
      page
        .getByRole("list", { name: "Library sources" })
        .getByRole("listitem")
        .filter({ hasText: listed!.filename }),
    ).toBeVisible();
  }
  await expect(
    page.locator(".tap-library-status[data-status=ready]"),
  ).toHaveCount(7);
  // The current Library proves browser-visible persisted documents. Answers
  // and citations are deliberately verified through the canonical Project API.
  const answerHttp = await page.request.post(`${knowledgePath}/answers`, {
    headers: { Origin: ORIGIN },
    data: {
      answerMode: "quick",
      query: policyQuestion(state.runId),
      sources: ["doc"],
      resourceRefs: [
        { family: "doc", sourceId: state.policy.sourceId, mode: "scope" },
      ],
    },
  });
  expect(answerHttp.status()).toBe(200);
  const answer = (await answerHttp.json()) as AnswerResponse;
  expect(answer.abstained).toBe(false);
  expect(answer.citations.length).toBeGreaterThan(0);
  expect(answer.claims.length).toBeGreaterThan(0);
  expect(
    answer.citations.every(
      (citation) => citation.source.sourceId === state.policy.sourceId,
    ),
  ).toBe(true);
  expect(
    answer.citations.some(
      (citation) => citation.source.sourceId === state.deleted.sourceId,
    ),
  ).toBe(false);
  for (const claim of answer.claims)
    expect(claim.citationIds.length).toBeGreaterThan(0);
  expect(
    answer.claims.filter((claim) =>
      claim.text.includes(`Tapper ${state.runId} refund requests`),
    ),
  ).toHaveLength(1);
  expect(externalRequests).toEqual([]);
  expect(pageErrors).toEqual([]);
  expect(consoleFailures).toEqual([]);
  expect(requestFailures).toEqual([]);
});
