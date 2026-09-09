import { expect, test } from "@playwright/test";

import { writeConversationState } from "./fixtureBuilder";

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

  const [agentsResponse, skillsResponse] = await Promise.all([
    page.request.get(`${root}/ai/agents`),
    page.request.get(`${root}/ai/skills`),
  ]);
  expect(agentsResponse.status()).toBe(200);
  expect(skillsResponse.status()).toBe(200);
  const agents = (await agentsResponse.json()) as {
    items: Array<{ revisionId: string; displayName: string }>;
  };
  const skills = (await skillsResponse.json()) as {
    items: Array<{ revisionId: string; displayName: string }>;
  };
  expect(agents.items.length).toBeGreaterThan(0);
  expect(skills.items.length).toBeGreaterThan(0);

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
    .getByRole("button", { name: /Select model, current model GPT-5\.6 Sol/u })
    .click();
  await page.getByRole("menuitemradio", { name: "GPT-5.6 Sol" }).click();
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
    .toMatch(/completed|abstained/u);

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
      "answer.delta",
      "turn.completed",
      "conversation.turn.completed",
    ]),
  );
  const resumeFrom = eventPage.items.find(
    (item) => item.eventType === "answer.delta",
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

  const citationButton = page.getByRole("button", { name: "引用 1" }).first();
  await expect(citationButton).toBeVisible();
  await citationButton.click();
  await expect(page.getByRole("heading", { name: "原文依据" })).toBeVisible();
  const sourceLink = page.getByRole("link", { name: "打开来源" });
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
  await page.locator("form.tap-composer").evaluate((form: HTMLFormElement) => {
    form.requestSubmit();
    form.requestSubmit();
  });
  expect((await appendResponse).status()).toBe(202);
  expect(appendRequests).toBe(1);

  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/conversations/${accepted.conversationId}`,
        );
        const body = (await response.json()) as {
          turns: Array<{ state: string }>;
        };
        return body.turns.length === 2 &&
          /completed|abstained/u.test(body.turns[1]!.state)
          ? body.turns.length
          : 0;
      },
      { timeout: 45_000 },
    )
    .toBe(2);

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
  const canceledResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname ===
        `${root}/conversations/${accepted.conversationId}/turns/${cancelAccepted.turnId}/cancel`,
  );
  await page.getByRole("button", { name: "Stop" }).click();
  const canceled = await canceledResponsePromise;
  expect(canceled.status()).toBe(200);
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
  await expect(
    page.getByText("Generation stopped.", { exact: true }),
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

  await page.getByRole("button", { name: "引用 1" }).first().click();
  await expect(page.getByRole("heading", { name: "原文依据" })).toBeVisible();
  await expect(
    page
      .getByLabel("原文", { exact: true })
      .getByText("verified identity evidence", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "关闭原文" }).click();

  const history = page.getByRole("navigation", { name: "Chat history" });
  await expect(
    history.getByRole("button", { name: new RegExp(prompt, "u") }),
  ).toBeVisible();
  await history.getByRole("button", { name: new RegExp(prompt, "u") }).click();
  await expect(
    page
      .getByRole("log", { name: "Conversation" })
      .getByText(prompt, { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Library", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible();
  await history.getByRole("button", { name: new RegExp(prompt, "u") }).click();
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
    modelAlias: "tapper-chat",
    prompt,
    skillLabel: skills.items[0]!.displayName,
    sourceId: createdSourceId,
    sourceLabel: sourceFilename,
    turnId: accepted.turnId,
  });
});
