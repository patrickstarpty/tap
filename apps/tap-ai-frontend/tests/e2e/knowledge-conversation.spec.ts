import { expect, test } from "@playwright/test";

import { writeConversationState } from "./fixtureBuilder";
import { preparePublishedFixture } from "./publicationFixture";

const ORIGIN = "http://127.0.0.1:15173";

test("durable Conversation uses approved context, resumes SSE, and restores in Tapper", async ({
  page,
}) => {
  const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeResponse.status()).toBe(200);
  const runtime = (await runtimeResponse.json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;

  const sourceFilename = `conversation-evidence-${Date.now()}.md`;
  const upload = await page.request.post(`${root}/knowledge/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `conversation-source-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: sourceFilename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          `# Underwriting evidence ${Date.now()}\n\nA policy application requires verified identity evidence.`,
        ),
      },
    },
  });
  expect(upload.status()).toBe(202);
  const uploaded = (await upload.json()) as {
    accepted: { document: { documentId: string } };
    source: { sourceId: string };
  };
  const createdDocumentId = uploaded.accepted.document.documentId;
  const createdSourceId = uploaded.source.sourceId;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/sources/${createdSourceId}?limit=50`,
        );
        const detail = (await response.json()) as { readyCount: number };
        return detail.readyCount;
      },
      { timeout: 45_000 },
    )
    .toBeGreaterThan(0);
  const detailResponse = await page.request.get(
    `${root}/knowledge/sources/${createdSourceId}?limit=50`,
  );
  expect(detailResponse.status()).toBe(200);
  const sourceDetail = (await detailResponse.json()) as {
    documents: { items: Array<{ revisionId: string; status: string }> };
  };
  const revision = sourceDetail.documents.items.find(
    (item) => item.status === "ready",
  );
  expect(revision).toBeDefined();
  preparePublishedFixture([revision!.revisionId]);

  const [agentsResponse, skillsResponse, modelsResponse] = await Promise.all([
    page.request.get(`${root}/ai/agents`),
    page.request.get(`${root}/ai/skills`),
    page.request.get(`${root}/ai/models`),
  ]);
  expect(agentsResponse.status()).toBe(200);
  expect(skillsResponse.status()).toBe(200);
  expect(modelsResponse.status()).toBe(200);
  const agents = (await agentsResponse.json()) as {
    items: Array<{ revisionId: string; displayName: string }>;
  };
  const skills = (await skillsResponse.json()) as {
    items: Array<{ revisionId: string; displayName: string }>;
  };
  const models = (await modelsResponse.json()) as {
    defaultAlias: string;
    items: Array<{ alias: string; displayName: string }>;
  };
  const defaultModel = models.items.find(
    (model) => model.alias === models.defaultAlias,
  );
  expect(agents.items.length).toBeGreaterThan(0);
  expect(skills.items.length).toBeGreaterThan(0);
  expect(defaultModel).toBeDefined();

  const prompt = `Durable conversation ${Date.now()}`;
  await page.goto("/");
  await page
    .getByRole("checkbox", { name: new RegExp(sourceFilename, "u") })
    .check();
  await page.getByRole("button", { name: "Add to message" }).click();
  await page.getByRole("menuitem", { name: "Use Agents" }).click();
  await page
    .getByRole("option", { name: agents.items[0]!.displayName })
    .click();
  await page.getByRole("button", { name: "Add to message" }).click();
  await page.getByRole("menuitem", { name: "Use Skills" }).click();
  await page
    .getByRole("option", { name: skills.items[0]!.displayName })
    .click();
  await page
    .getByRole("button", {
      name: `Select model, current model ${defaultModel!.displayName}`,
    })
    .click();
  await page
    .getByRole("menuitemradio", { name: defaultModel!.displayName })
    .click();
  await page.getByRole("textbox", { name: "Message Tapper" }).fill(prompt);
  const acceptedResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${root}/conversations`,
  );
  await page.getByRole("button", { name: "Send" }).click();
  const acceptedResponse = await acceptedResponsePromise;
  expect(acceptedResponse.status()).toBe(202);
  const accepted = (await acceptedResponse.json()) as {
    conversationId: string;
    turnId: string;
  };

  // The generating draft (`answer.draft` frames assembled into `DraftAnswer`)
  // must be visible, with at least one inline citation badge resolved from
  // `retrieval.hits_ready`, before the turn reaches a terminal state and the
  // final `GroundedAnswer` replaces it.
  await expect(page.locator(".tapper-draft-answer").first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.locator(".tapper-draft-citation").first()).toBeVisible({
    timeout: 15_000,
  });

  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/conversations/${accepted.conversationId}`,
        );
        const body = (await response.json()) as {
          turns: Array<{ state: string }>;
        };
        return body.turns[0]?.state;
      },
      { timeout: 45_000 },
    )
    .toMatch(/completed|abstained/u);

  // AnswerFeedback only renders for a "completed" turn (shouldShowAnswerFeedback
  // in TapperWorkspace.tsx); the preceding poll also accepts "abstained", so
  // assert the stricter state explicitly here to fail fast with a clear
  // message if the fixture produced an abstained answer instead.
  const preFeedbackStateResponse = await page.request.get(
    `${root}/conversations/${accepted.conversationId}`,
  );
  const preFeedbackStateBody = (await preFeedbackStateResponse.json()) as {
    turns: Array<{ state: string }>;
  };
  expect(preFeedbackStateBody.turns[0]?.state).toBe("completed");

  // The draft is gone now that the turn is terminal and the final answer
  // (with its own "Open source citation 1" button, asserted further below)
  // has replaced it.
  await expect(page.locator(".tapper-draft-answer")).toHaveCount(0);

  const notHelpfulButton = page.getByRole("button", {
    name: /^(Not helpful|没帮助)$/u,
  });
  await expect(notHelpfulButton).toBeVisible();
  await notHelpfulButton.click();
  const feedbackComment = `feedback comment ${Date.now()}`;
  await page
    .getByRole("textbox", {
      name: /^(Add a comment \(optional\)|补充说明（可选）)$/u,
    })
    .fill(feedbackComment);
  const feedbackSubmitted = page.waitForResponse(
    (response) =>
      response.request().method() === "PUT" &&
      new URL(response.url()).pathname ===
        `${root}/conversations/${accepted.conversationId}/turns/${accepted.turnId}/feedback`,
  );
  await page.getByRole("button", { name: /^(Submit|提交)$/u }).click();
  expect((await feedbackSubmitted).status()).toBe(200);
  await page.reload();
  await expect(
    page.getByRole("button", { name: /^(Not helpful|没帮助)$/u }),
  ).toHaveAttribute("aria-pressed", "true");
  const feedbackCheckResponse = await page.request.get(
    `${root}/conversations/${accepted.conversationId}`,
  );
  const feedbackCheckBody = (await feedbackCheckResponse.json()) as {
    turns: Array<{
      turnId: string;
      myFeedback: { rating: string; comment: string | null } | null;
    }>;
  };
  const feedbackTurn = feedbackCheckBody.turns.find(
    (turn) => turn.turnId === accepted.turnId,
  );
  expect(feedbackTurn?.myFeedback?.rating).toBe("down");
  expect(feedbackTurn?.myFeedback?.comment).toBe(feedbackComment);

  const traceResponse = await page.request.get(
    `${root}/conversations/${accepted.conversationId}/turns/${accepted.turnId}/trace`,
  );
  expect(traceResponse.status()).toBe(200);
  const trace = (await traceResponse.json()) as {
    summary: {
      inputTokens: number;
      outputTokens: number;
    };
  };
  expect(trace.summary.inputTokens).toBeGreaterThan(0);
  expect(trace.summary.outputTokens).toBeGreaterThan(0);

  const tracePanelButton = page.getByRole("button", {
    name: /^(Trace|调用链):/u,
  });
  await expect(tracePanelButton).toBeVisible({ timeout: 15_000 });
  await expect(tracePanelButton).toHaveText(
    new RegExp(
      `${trace.summary.inputTokens}/${trace.summary.outputTokens} tokens`,
      "u",
    ),
  );
  await expect(tracePanelButton).toHaveText(/成本未知|cost unknown/u);
  await tracePanelButton.click();
  await expect(
    page
      .locator(".tap-trace-row-name")
      .filter({ hasText: /^(Turn execution|回合执行)$/u }),
  ).toBeVisible();
  await expect(
    page
      .locator(".tap-trace-row-name")
      .filter({ hasText: /^(Retrieval search|检索)$/u }),
  ).toBeVisible();
  const modelCallRow = page.locator(".tap-trace-row").filter({
    has: page.locator(".tap-trace-row-name", { hasText: /^chat /u }),
  });
  await expect(modelCallRow.first()).toBeVisible();
  await modelCallRow
    .first()
    .getByRole("button", { name: /View call|查看调用/u })
    .click();
  const requestTab = page.getByRole("tab", { name: /^(Request|请求)$/u });
  await expect(requestTab).toBeVisible();
  await expect(requestTab).toHaveAttribute("aria-selected", "true");
  const requestContent = page.locator("pre");
  await expect(requestContent).toBeVisible();
  await expect(requestContent).not.toHaveText("");
  await page.getByRole("button", { name: /^(Close|关闭)$/u }).click();

  const eventsResponse = await page.request.get(
    `${root}/conversations/${accepted.conversationId}/events`,
  );
  expect(eventsResponse.status()).toBe(200);
  const eventPage = (await eventsResponse.json()) as {
    items: Array<{
      sequence: number;
      eventType: string;
      payload: { answer?: { citations?: Array<{ citationId: string }> } };
    }>;
  };
  expect(eventPage.items.map((item) => item.eventType)).toEqual(
    expect.arrayContaining([
      "conversation.turn.requested",
      "retrieval.hits_ready",
      "turn.completed",
      "conversation.turn.completed",
    ]),
  );
  // `answer.draft` SSE frames carry no MySQL sequence (PR 3) and are never
  // persisted as `ChatEventEnvelope` rows, so resume uses the durable
  // `retrieval.hits_ready` event's sequence as the Last-Event-ID instead.
  const resumeFrom = eventPage.items.find(
    (item) => item.eventType === "retrieval.hits_ready",
  )!.sequence;
  const citationId = eventPage.items.find(
    (item) => item.eventType === "turn.completed",
  )?.payload.answer?.citations?.[0]?.citationId;
  expect(citationId).toBeTruthy();
  const resumed = await page.evaluate(
    async ({ path, sequence }) => {
      const controller = new AbortController();
      const response = await fetch(path, {
        headers: { "Last-Event-ID": String(sequence) },
        signal: controller.signal,
      });
      const first = await response.body!.getReader().read();
      controller.abort();
      return {
        status: response.status,
        text: new TextDecoder().decode(first.value),
      };
    },
    {
      path: `${ORIGIN}${root}/conversations/${accepted.conversationId}/stream`,
      sequence: resumeFrom,
    },
  );
  expect(resumed.status).toBe(200);
  expect(resumed.text).not.toContain(`id: ${resumeFrom}\n`);

  const citationButton = page
    .getByRole("button", {
      name: /Open source citation 1|打开来源引用 1/u,
    })
    .first();
  await expect(citationButton).toBeVisible();
  await citationButton.click();
  await expect(
    page.getByRole("heading", { name: /Cited evidence|引用依据/u }),
  ).toBeVisible();
  const sourceLink = page.getByRole("link", {
    name: /Open source|打开来源/u,
  });
  await expect(sourceLink).toHaveAttribute(
    "href",
    `#source-${createdSourceId}`,
  );
  await sourceLink.click();
  await expect(page.locator(`#source-${createdSourceId}`)).toBeVisible();

  const followUp = `${prompt} follow-up`;
  await page.getByRole("textbox", { name: "Message Tapper" }).fill(followUp);
  let appendRequests = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname ===
        `${root}/conversations/${accepted.conversationId}/turns`
    )
      appendRequests += 1;
  });
  const appendResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `${root}/conversations/${accepted.conversationId}/turns`,
  );
  const resumedAppendStream = page.waitForRequest(
    (request) =>
      request.method() === "GET" &&
      new URL(request.url()).pathname ===
        `${root}/conversations/${accepted.conversationId}/stream` &&
      Number(request.headers()["last-event-id"] ?? 0) > 0,
  );
  await page.locator("form.tap-composer").evaluate((form: HTMLFormElement) => {
    form.requestSubmit();
    form.requestSubmit();
  });
  expect((await appendResponse).status()).toBe(202);
  expect(appendRequests).toBe(1);
  expect(Number((await resumedAppendStream).headers()["last-event-id"])).toBe(
    eventPage.items.at(-1)!.sequence,
  );
  const appendedTurn = page.locator(".tap-turn").filter({ hasText: followUp });
  await expect(appendedTurn).toHaveCount(1);
  await expect(
    appendedTurn.getByRole("button", {
      name: /Open source citation 1|打开来源引用 1/u,
    }),
  ).toBeVisible({ timeout: 45_000 });
  await expect(
    appendedTurn.getByText("Tapper is grounding the answer…", { exact: true }),
  ).toHaveCount(0);

  await page
    .getByRole("textbox", { name: "Message Tapper" })
    .fill(`${prompt} [e2e-cancel]`);
  const cancelAcceptedResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `${root}/conversations/${accepted.conversationId}/turns`,
  );
  await page.getByRole("button", { name: "Send" }).click();
  const cancelAcceptedResponse = await cancelAcceptedResponsePromise;
  expect(cancelAcceptedResponse.status()).toBe(202);
  const cancelAccepted = (await cancelAcceptedResponse.json()) as {
    turnId: string;
  };
  const stop = page.getByRole("button", { name: "Stop generating" });
  await expect(stop).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toHaveCount(0);
  // The `[e2e-cancel]` deterministic gateway hook (Task 9) paces every
  // piece but the last at the normal 0.02s cadence before holding the long
  // cancellation delay, so a non-empty draft chunk has already cleared
  // `DraftBatcher`'s holdback margin and reached Redis by the time Stop is
  // clickable -- this must be visible before Stop is clicked, not just
  // after.
  await expect(page.locator(".tapper-draft-answer").first()).toBeVisible({
    timeout: 15_000,
  });
  const canceledPromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `${root}/conversations/${accepted.conversationId}/turns/${cancelAccepted.turnId}/cancel`,
  );
  await stop.click();
  expect((await canceledPromise).status()).toBe(200);
  await expect
    .poll(async () => {
      const response = await page.request.get(
        `${root}/conversations/${accepted.conversationId}`,
      );
      const body = (await response.json()) as {
        turns: Array<{ turnId: string; state: string }>;
      };
      return body.turns.find((turn) => turn.turnId === cancelAccepted.turnId)
        ?.state;
    })
    .toBe("canceled");
  // `DraftAnswer`'s canceled view (Task 9): the partial draft's "Stopped ·
  // not verified" / "已停止 · 未核对依据" `role="status"` notice replaces
  // the "Stop generating" control; the partial draft never joins the
  // citation system, so no citation badge from the canceled turn is ever
  // shown.
  await expect(
    page.getByText(/^(Stopped · not verified|已停止 · 未核对依据)$/u, {
      exact: true,
    }),
  ).toBeVisible();

  const deleted = await page.request.delete(
    `${root}/knowledge/sources/${createdSourceId}`,
    {
      headers: {
        Origin: ORIGIN,
        "Idempotency-Key": `cleanup-${createdSourceId}`,
      },
    },
  );
  expect(deleted.status()).toBe(204);
  await expect
    .poll(async () => {
      const response = await page.request.get(
        `${root}/knowledge/documents?limit=50`,
      );
      const body = (await response.json()) as {
        items: Array<{ documentId: string }>;
      };
      return body.items.some((item) => item.documentId === createdDocumentId);
    })
    .toBe(false);

  await page
    .getByRole("button", {
      name: /Open source citation 1|打开来源引用 1/u,
    })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: /Cited evidence|引用依据/u }),
  ).toBeVisible();
  await expect(
    page
      .getByLabel(/Cited content|引用内容/u)
      .getByText("verified identity evidence", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Close cited content|关闭引用内容/u })
    .click();

  const history = page.getByRole("navigation", { name: "Chat history" });
  await expect(
    history.getByRole("button", { name: new RegExp(`^${prompt}`, "u") }),
  ).toBeVisible();
  await history
    .getByRole("button", { name: new RegExp(`^${prompt}`, "u") })
    .click();
  await expect(
    page
      .getByRole("log", { name: "Conversation" })
      .getByText(prompt, { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
  await history
    .getByRole("button", { name: new RegExp(`^${prompt}`, "u") })
    .click();
  await expect(
    page
      .getByRole("log", { name: "Conversation" })
      .getByText(prompt, { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page
      .getByRole("log", { name: "Conversation" })
      .getByText(prompt, { exact: true }),
  ).toBeVisible();
  await writeConversationState({
    agentLabel: agents.items[0]!.displayName,
    citationId: citationId!,
    conversationId: accepted.conversationId,
    modelAlias: models.defaultAlias,
    prompt,
    skillLabel: skills.items[0]!.displayName,
    sourceId: createdSourceId,
    sourceLabel: sourceFilename,
    turnId: accepted.turnId,
  });
});

test("a published source's suggested question grounds a Tapper answer", async ({
  page,
}) => {
  const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeResponse.status()).toBe(200);
  const runtime = (await runtimeResponse.json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;

  const sourceFilename = `suggestion-evidence-${Date.now()}.md`;
  const upload = await page.request.post(`${root}/knowledge/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `suggestion-source-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: sourceFilename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          `# Suggested question evidence ${Date.now()}\n\nA policy application requires verified identity evidence.`,
        ),
      },
    },
  });
  expect(upload.status()).toBe(202);
  const uploaded = (await upload.json()) as {
    accepted: { document: { documentId: string } };
    source: { sourceId: string };
  };
  const createdSourceId = uploaded.source.sourceId;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/sources/${createdSourceId}?limit=50`,
        );
        const detail = (await response.json()) as { readyCount: number };
        return detail.readyCount;
      },
      { timeout: 45_000 },
    )
    .toBeGreaterThan(0);
  const detailResponse = await page.request.get(
    `${root}/knowledge/sources/${createdSourceId}?limit=50`,
  );
  expect(detailResponse.status()).toBe(200);
  const sourceDetail = (await detailResponse.json()) as {
    documents: { items: Array<{ revisionId: string; status: string }> };
  };
  const revision = sourceDetail.documents.items.find(
    (item) => item.status === "ready",
  );
  expect(revision).toBeDefined();
  preparePublishedFixture([revision!.revisionId]);

  // The suggestion worker generates candidates asynchronously from published
  // sources; the first read after publish returns an empty page and enqueues
  // a refresh, so poll by reloading until the worker has produced items.
  // Once the conversation list loads, the workspace restores the most recent
  // conversation from earlier tests, so wait for that list and then open a
  // new chat explicitly before looking for suggestions.
  const suggestionsGroup = page.getByRole("group", {
    name: "Suggested questions",
  });
  async function suggestionsReady(): Promise<boolean> {
    const conversationList = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === `${root}/conversations`,
    );
    await page.reload();
    await conversationList;
    await page.getByRole("button", { name: "New chat", exact: true }).click();
    try {
      await suggestionsGroup.waitFor({ state: "visible", timeout: 3_000 });
    } catch {
      return false;
    }
    return (await suggestionsGroup.getByRole("button").count()) > 0;
  }
  await page.goto("/");
  await expect
    .poll(() => suggestionsReady(), { timeout: 60_000, intervals: [2_000] })
    .toBe(true);

  const firstSuggestion = suggestionsGroup.getByRole("button").first();
  await firstSuggestion.click();
  const composer = page.getByRole("textbox", { name: "Message Tapper" });
  await expect(composer).not.toHaveValue("");

  const acceptedResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${root}/conversations`,
  );
  await page.getByRole("button", { name: "Send" }).click();
  const acceptedResponse = await acceptedResponsePromise;
  expect(acceptedResponse.status()).toBe(202);
  const accepted = (await acceptedResponse.json()) as {
    conversationId: string;
    turnId: string;
  };

  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/conversations/${accepted.conversationId}`,
        );
        const body = (await response.json()) as {
          turns: Array<{ state: string }>;
        };
        return body.turns[0]?.state;
      },
      { timeout: 45_000 },
    )
    .toBe("completed");

  const eventsResponse = await page.request.get(
    `${root}/conversations/${accepted.conversationId}/events`,
  );
  expect(eventsResponse.status()).toBe(200);
  const eventPage = (await eventsResponse.json()) as {
    items: Array<{
      eventType: string;
      payload: {
        answer?: {
          abstained?: boolean;
          citations?: Array<{ citationId: string }>;
        };
      };
    }>;
  };
  const completedEvent = eventPage.items.find(
    (item) => item.eventType === "turn.completed",
  );
  expect(completedEvent?.payload.answer?.abstained).toBe(false);
  expect(
    completedEvent?.payload.answer?.citations?.length ?? 0,
  ).toBeGreaterThan(0);
  await expect(
    page
      .getByRole("button", {
        name: /Open source citation 1|打开来源引用 1/u,
      })
      .first(),
  ).toBeVisible({ timeout: 5_000 });

  const deleted = await page.request.delete(
    `${root}/knowledge/sources/${createdSourceId}`,
    {
      headers: {
        Origin: ORIGIN,
        "Idempotency-Key": `suggestion-cleanup-${createdSourceId}`,
      },
    },
  );
  expect(deleted.status()).toBe(204);
});

test("an abstained answer replaces the draft", async ({ page }) => {
  // `AuthorizedRetrieval._hit_is_in_execution` (retrieve.py) admits every
  // hit whose family/corpus/embedding version match once no `SCOPE`
  // resource narrows the search, so an unrestricted question almost
  // always finds *some* evidence in whatever the project already has
  // published -- there is no content-only way to force a genuinely
  // empty-evidence abstain deterministically against a shared E2E corpus.
  // The isolated browser E2E instead appends ` [e2e-abstain]` to the
  // question, a test-only marker `DeterministicModelGateway` recognizes
  // (Task 9 fix round 1, `apps/tap-ai-backend/src/tap/testing/
  // deterministic_model_gateway.py`): it forces the fake model's answer to
  // carry no inline `[S1]`/`[R1]` markers at all, exactly the shape
  // `parse_inline_answer` (and a real model, per `KnowledgeModelGateway`'s
  // platform instruction) already treats as "no grounded claim" --
  // `verify_answer`'s `if not generation.text and not claims` then
  // abstains deterministically regardless of what evidence was retrieved.
  const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
  expect(runtimeResponse.status()).toBe(200);
  const runtime = (await runtimeResponse.json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;

  const sourceFilename = `abstain-evidence-${Date.now()}.md`;
  const upload = await page.request.post(`${root}/knowledge/sources`, {
    headers: {
      Origin: ORIGIN,
      "Idempotency-Key": `abstain-source-${Date.now()}`,
    },
    multipart: {
      upload: {
        name: sourceFilename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          `# Abstain evidence ${Date.now()}\n\nA policy application requires verified identity evidence.`,
        ),
      },
    },
  });
  expect(upload.status()).toBe(202);
  const uploaded = (await upload.json()) as { source: { sourceId: string } };
  const createdSourceId = uploaded.source.sourceId;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/sources/${createdSourceId}?limit=50`,
        );
        const detail = (await response.json()) as { readyCount: number };
        return detail.readyCount;
      },
      { timeout: 45_000 },
    )
    .toBeGreaterThan(0);
  const detailResponse = await page.request.get(
    `${root}/knowledge/sources/${createdSourceId}?limit=50`,
  );
  const sourceDetail = (await detailResponse.json()) as {
    documents: { items: Array<{ revisionId: string; status: string }> };
  };
  const revision = sourceDetail.documents.items.find(
    (item) => item.status === "ready",
  );
  expect(revision).toBeDefined();
  preparePublishedFixture([revision!.revisionId]);

  await page.goto("/");
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await page
    .getByRole("checkbox", { name: new RegExp(sourceFilename, "u") })
    .check();

  const prompt = `Abstained question ${Date.now()} [e2e-abstain]`;
  await page.getByRole("textbox", { name: "Message Tapper" }).fill(prompt);
  const acceptedResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${root}/conversations`,
  );
  await page.getByRole("button", { name: "Send" }).click();
  const acceptedResponse = await acceptedResponsePromise;
  expect(acceptedResponse.status()).toBe(202);
  const accepted = (await acceptedResponse.json()) as {
    conversationId: string;
    turnId: string;
  };

  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/conversations/${accepted.conversationId}`,
        );
        const body = (await response.json()) as {
          turns: Array<{ state: string }>;
        };
        return body.turns[0]?.state;
      },
      { timeout: 45_000 },
    )
    .toMatch(/abstained|completed/u);

  const stateResponse = await page.request.get(
    `${root}/conversations/${accepted.conversationId}`,
  );
  const stateBody = (await stateResponse.json()) as {
    turns: Array<{ state: string }>;
  };
  // Fail fast with a clear message if the fixture unexpectedly grounded
  // this question instead of abstaining.
  expect(stateBody.turns[0]?.state).toBe("abstained");

  const eventsResponse = await page.request.get(
    `${root}/conversations/${accepted.conversationId}/events`,
  );
  expect(eventsResponse.status()).toBe(200);
  const eventPage = (await eventsResponse.json()) as {
    items: Array<{
      eventType: string;
      payload: { answer?: { abstained?: boolean } };
    }>;
  };
  const abstainedEvent = eventPage.items.find(
    (item) => item.eventType === "turn.abstained",
  );
  expect(abstainedEvent?.payload.answer?.abstained).toBe(true);

  // `GroundedAnswer`'s abstained view (an `Alert` titled from `ANSWER_COPY`)
  // has replaced whatever was showing while the turn was running -- the
  // draft is gone and the "Stop generating"/`DraftAnswer` machinery never
  // lingers past a terminal abstained state.
  await expect(
    page.getByText(
      /^(The selected sources do not contain enough evidence to answer this question\.|所选来源中没有足够证据回答这个问题。)$/u,
    ),
  ).toBeVisible();
  await expect(page.locator(".tapper-draft-answer")).toHaveCount(0);

  const deleted = await page.request.delete(
    `${root}/knowledge/sources/${createdSourceId}`,
    {
      headers: {
        Origin: ORIGIN,
        "Idempotency-Key": `abstain-cleanup-${createdSourceId}`,
      },
    },
  );
  expect(deleted.status()).toBe(204);
});
