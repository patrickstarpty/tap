import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  document,
  fakeKnowledgeClient,
  type FakeKnowledgeClient,
} from "../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../features/knowledge/testing/renderKnowledgeApp";
import { createTestQueryClient } from "../shared/testing/renderApp";
import { TapAiPage } from "./TapAiPage";

it("shows only TAP AI product modules", () => {
  renderKnowledgeApp(<TapAiPage />, {
    api: fakeKnowledgeClient(),
  });
  const product = screen.getByRole("navigation", { name: "Product" });
  expect(screen.getByRole("img", { name: "TAP AI" })).toBeVisible();
  expect(
    within(product)
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label")),
  ).toEqual(["Tapper", "Test Management"]);
});

it("does not expose the legacy TAP automation and analytics workspace", async () => {
  renderTapAiWithTestPlans(fakeKnowledgeClient());
  await userEvent.click(
    screen.getByRole("button", { name: "Test Management" }),
  );
  expect(screen.queryByText("TP-101")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Low Code Automation" }),
  ).not.toBeInTheDocument();
});

it("does not offer the legacy automation workflow in TAP AI chat", async () => {
  renderKnowledgeApp(<TapAiPage />, {
    api: fakeKnowledgeClient(),
  });
  const user = userEvent.setup();
  await user.type(
    screen.getByRole("textbox", { name: "Message Tapper" }),
    "Generate automation script for login",
  );
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(
    screen.queryByRole("button", { name: "Create Test Plan first" }),
  ).not.toBeInTheDocument();
});

it("does not show hard-coded quick prompts on a new chat", () => {
  renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });
  expect(
    screen.queryByText("Summarize the life insurance underwriting rules"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("group", { name: "Suggested prompts" }),
  ).not.toBeInTheDocument();
});

it("shows a neutral workspace identity in the sidebar", () => {
  renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });
  expect(screen.getByText("Local workspace")).toBeVisible();
  expect(screen.queryByText("Prototype team")).not.toBeInTheDocument();
  expect(screen.queryByText("PT")).not.toBeInTheDocument();
});

// Below: tests migrated from the deleted src/pages/TapperPage.tsx +
// TapperPage.test.tsx. TapAiPage always renders TapProductPrototype in
// "api" (durable) mode now, so every case here exercises that mode; see
// docs referenced in the task report for which assertions changed and why.

function withKnowledgeSources() {
  return fakeKnowledgeClient().withPublishedSources({
    items: [
      {
        sourceId: "src_life_underwriting_rules",
        sourceName: "life-underwriting-rules.md",
        documentId: "doc_life_underwriting_rules",
        filename: "life-underwriting-rules.md",
        revisionId: "rev_life_underwriting_rules",
        publicationId: "pub_life_underwriting_rules",
        approvedItemCount: 1,
        inventoryItemCount: 1,
        partial: false,
        expiresAt: "2027-01-01T00:00:00Z",
      },
      {
        sourceId: "src_health_disclosure_guide",
        sourceName: "health-disclosure-guide.pdf",
        documentId: "doc_health_disclosure_guide",
        filename: "health-disclosure-guide.pdf",
        revisionId: "rev_health_disclosure_guide",
        publicationId: "pub_health_disclosure_guide",
        approvedItemCount: 1,
        inventoryItemCount: 1,
        partial: false,
        expiresAt: "2027-01-01T00:00:00Z",
      },
    ],
  });
}

// TapProductPrototype's durable/api Test Management view renders the real
// TestPlanLibrary component (instead of the fixture-only placeholder text).
// Its Ant Design Spin uses a deprecated `tip` prop that logs a console
// warning while its test-plans query is pending; pre-seeding that query
// avoids the pending state so switching into Test Management under api mode
// doesn't trip the repo's "no unexpected console output" test guard. This is
// a pre-existing, unrelated antd usage issue in TestPlanLibrary.tsx, out of
// scope for this task (see task-3-report.md).
function renderTapAiWithTestPlans(api: FakeKnowledgeClient) {
  const queryClient = createTestQueryClient();
  queryClient.setQueryData(["test-plans", api.projectId], []);
  return renderKnowledgeApp(<TapAiPage />, { api, queryClient });
}

async function sendMessage(
  user: ReturnType<typeof userEvent.setup>,
  text: string,
) {
  const composer = screen.getByRole("textbox", { name: "Message Tapper" });
  await user.clear(composer);
  await user.type(composer, text);
  await user.click(screen.getByRole("button", { name: "Send" }));
}

const SHA_A = `sha256:${"a".repeat(64)}`;

/**
 * Stubs the durable Conversation API for a brand-new ("draft") conversation:
 * an empty history list, followed by a POST /conversations that mints a
 * fresh conversation/turn pair, and matching GET detail/events/stream
 * endpoints so the optimistic Turn survives the follow-up fetches the
 * component makes once it adopts the new conversation id.
 */
function stubDraftConversation() {
  let nextId = 1;
  const summaries: Array<{
    conversationId: string;
    title: string;
    createdAt: string;
    updatedAt: string;
  }> = [];
  const detailsById = new Map<string, unknown>();
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const request = input instanceof Request ? input : new Request(input);
    const url = new URL(request.url);
    const path = url.pathname;
    if (/\/ai\/(agents|skills)$/u.test(request.url)) {
      return Response.json({ items: [] });
    }
    if (path.endsWith("/conversations") && request.method === "GET") {
      return Response.json({ items: summaries, nextCursor: null });
    }
    if (path.endsWith("/conversations") && request.method === "POST") {
      const body = (await request.json()) as {
        message: string;
        modelAlias: string;
        sourceRevisionIds?: string[];
        agentRevisionId?: string | null;
        skillRevisionIds?: string[];
      };
      const conversationId = `conversation-${nextId}`;
      const turnId = `conversation-${nextId}-turn-1`;
      nextId += 1;
      summaries.unshift({
        conversationId,
        title: body.message,
        createdAt: "2026-09-29T08:00:00Z",
        updatedAt: "2026-09-29T08:00:00Z",
      });
      detailsById.set(conversationId, {
        conversationId,
        title: body.message,
        createdAt: "2026-09-29T08:00:00Z",
        updatedAt: "2026-09-29T08:00:00Z",
        turns: [
          {
            turnId,
            state: "queued",
            attempt: 1,
            inputSnapshotDigest: SHA_A,
            answerEvidenceSnapshotDigest: null,
            input: {
              message: body.message,
              modelAlias: body.modelAlias,
              sourceRevisionIds: body.sourceRevisionIds ?? [],
              documentRevisionIds: [],
              resolvedResources: [],
              agentRevisionId: body.agentRevisionId ?? null,
              agentLabel: null,
              skillRevisionIds: body.skillRevisionIds ?? [],
              skillLabels: [],
            },
          },
        ],
      });
      return Response.json({ conversationId, turnId, state: "queued" }, {
        status: 202,
      });
    }
    const detailMatch = /\/conversations\/([^/]+)$/u.exec(path);
    if (detailMatch && request.method === "GET") {
      const detail = detailsById.get(detailMatch[1]!);
      if (detail !== undefined) return Response.json(detail);
    }
    if (path.endsWith("/events")) return Response.json({ items: [] });
    if (path.endsWith("/stream")) {
      return new Response("", {
        headers: { "content-type": "text/event-stream" },
      });
    }
    return Response.json({ items: [] });
  });
}

const NO_CONTEXT_ANSWER = {
  traceId: "trace-1",
  queryPlanId: "plan-1",
  contextSnapshotId: "context-1",
  corpusVersion: "v1",
  retrievalProfileId: "quick",
  degradedMode: false,
  answer: "Here is what is currently known.",
  abstained: false,
  claims: [
    {
      claimId: "claim-1",
      text: "Here is what is currently known.",
      citationIds: [],
    },
  ],
  citations: [],
};

/**
 * Like stubDraftConversation, but the newly created conversation is
 * immediately reported as completed with a grounded answer (no sources,
 * Agent, or Skill attached). TapProductPrototype only renders the
 * "no knowledge context was selected" notice once a Turn has a response, so
 * cases asserting that notice need this fuller round trip instead of the
 * plain queued Turn stubDraftConversation leaves behind.
 */
function stubImmediatelyAnsweredConversation() {
  let nextId = 1;
  const summaries: Array<{
    conversationId: string;
    title: string;
    createdAt: string;
    updatedAt: string;
  }> = [];
  const detailsById = new Map<string, unknown>();
  const eventsById = new Map<string, unknown>();
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const request = input instanceof Request ? input : new Request(input);
    const url = new URL(request.url);
    const path = url.pathname;
    if (/\/ai\/(agents|skills)$/u.test(request.url)) {
      return Response.json({ items: [] });
    }
    if (path.endsWith("/conversations") && request.method === "GET") {
      return Response.json({ items: summaries, nextCursor: null });
    }
    if (path.endsWith("/conversations") && request.method === "POST") {
      const body = (await request.json()) as {
        message: string;
        modelAlias: string;
      };
      const conversationId = `conversation-${nextId}`;
      const turnId = `conversation-${nextId}-turn-1`;
      nextId += 1;
      summaries.unshift({
        conversationId,
        title: body.message,
        createdAt: "2026-09-29T08:00:00Z",
        updatedAt: "2026-09-29T08:00:00Z",
      });
      detailsById.set(conversationId, {
        conversationId,
        title: body.message,
        createdAt: "2026-09-29T08:00:00Z",
        updatedAt: "2026-09-29T08:00:00Z",
        turns: [
          {
            turnId,
            state: "completed",
            attempt: 1,
            inputSnapshotDigest: SHA_A,
            answerEvidenceSnapshotDigest: `sha256:${"b".repeat(64)}`,
            input: {
              message: body.message,
              modelAlias: body.modelAlias,
              sourceRevisionIds: [],
              documentRevisionIds: [],
              resolvedResources: [],
              agentRevisionId: null,
              agentLabel: null,
              skillRevisionIds: [],
              skillLabels: [],
            },
          },
        ],
      });
      eventsById.set(conversationId, {
        items: [
          {
            eventId: "event-1",
            sequence: 1,
            turnId,
            occurredAt: "2026-09-29T08:00:01Z",
            eventType: "turn.completed",
            payload: { answer: NO_CONTEXT_ANSWER },
          },
        ],
      });
      return Response.json({ conversationId, turnId, state: "queued" }, {
        status: 202,
      });
    }
    const detailMatch = /\/conversations\/([^/]+)$/u.exec(path);
    if (detailMatch && request.method === "GET") {
      const detail = detailsById.get(detailMatch[1]!);
      if (detail !== undefined) return Response.json(detail);
    }
    const eventsMatch = /\/conversations\/([^/]+)\/events$/u.exec(path);
    if (eventsMatch) {
      const events = eventsById.get(eventsMatch[1]!);
      if (events !== undefined) return Response.json(events);
      return Response.json({ items: [] });
    }
    if (path.endsWith("/stream")) {
      return new Response("", {
        headers: { "content-type": "text/event-stream" },
      });
    }
    return Response.json({ items: [] });
  });
}

describe("TAP AI page (durable Conversation API)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("restores the default Tapper page from durable Conversation APIs, not localStorage", async () => {
    window.localStorage.setItem(
      "tap.prototype.workspace.v2",
      JSON.stringify({
        version: 2,
        activeConversationId: "local-only",
        conversations: [
          {
            id: "local-only",
            title: "Local-only prompt",
            turns: [],
            modelId: "tapper-chat",
            selectedSourceIds: [],
            selectedAgentIds: [],
            selectedSkillIds: [],
          },
        ],
        artifacts: { automations: [], testPlans: [], runs: [] },
      }),
    );
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(input);
      if (/\/ai\/(agents|skills)$/u.test(request.url)) {
        return Response.json({ items: [] });
      }
      if (request.url.endsWith("/conversations?limit=20")) {
        return Response.json({
          items: [
            {
              conversationId: "conversation-1",
              title: "Durable prompt",
              createdAt: "2026-09-09T00:00:00Z",
              updatedAt: "2026-09-09T00:00:01Z",
            },
            {
              conversationId: "conversation-2",
              title: "Older durable prompt",
              createdAt: "2026-09-08T00:00:00Z",
              updatedAt: "2026-09-08T00:00:01Z",
            },
          ],
          nextCursor: null,
        });
      }
      if (request.url.endsWith("/conversation-1/events")) {
        return Response.json({
          items: [
            {
              eventId: "event-1",
              sequence: 1,
              turnId: "turn-1",
              eventType: "turn.abstained",
              payload: {
                answer: {
                  traceId: "trace-1",
                  queryPlanId: "plan-1",
                  contextSnapshotId: "context-1",
                  corpusVersion: "v1",
                  retrievalProfileId: "quick",
                  degradedMode: false,
                  answer: "",
                  abstained: true,
                  abstentionReason: "insufficient_evidence",
                  claims: [],
                  citations: [],
                },
              },
              occurredAt: "2026-09-09T00:00:01Z",
            },
          ],
        });
      }
      if (request.url.endsWith("/conversation-1/stream")) {
        return new Response("", {
          headers: { "content-type": "text/event-stream" },
        });
      }
      if (request.url.endsWith("/conversation-1")) {
        return Response.json({
          conversationId: "conversation-1",
          title: "Durable prompt",
          createdAt: "2026-09-09T00:00:00Z",
          updatedAt: "2026-09-09T00:00:01Z",
          turns: [
            {
              turnId: "turn-1",
              state: "abstained",
              attempt: 1,
              inputSnapshotDigest: `sha256:${"1".repeat(64)}`,
              answerEvidenceSnapshotId: "answer-1",
              answerEvidenceSnapshotDigest: `sha256:${"2".repeat(64)}`,
              input: {
                message: "Durable prompt",
                modelAlias: "tapper-chat",
                sourceRevisionIds: [],
                documentRevisionIds: [],
                agentRevisionId: null,
                skillRevisionIds: [],
              },
            },
          ],
        });
      }
      return Response.json({ items: [] });
    });

    const first = renderKnowledgeApp(<TapAiPage />, {
      api: fakeKnowledgeClient(),
    });
    expect(
      within(
        await screen.findByRole("log", { name: "Conversation" }),
      ).getByText("Durable prompt"),
    ).toBeVisible();
    expect(screen.queryByText("Local-only prompt")).not.toBeInTheDocument();
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: /^Older durable prompt/u }),
    ).toBeVisible();
    first.unmount();
    renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });
    await waitFor(() =>
      expect(
        within(screen.getByRole("log", { name: "Conversation" })).getByText(
          "Durable prompt",
        ),
      ).toBeVisible(),
    );
  });

  it("never substitutes prototype copy when a completed API Turn has no answer evidence event", async () => {
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(input);
      if (/\/ai\/(agents|skills)$/u.test(request.url))
        return Response.json({ items: [] });
      if (request.url.endsWith("/conversations?limit=20")) {
        return Response.json({
          items: [
            {
              conversationId: "conversation-1",
              title: "No evidence",
              createdAt: "2026-09-09T00:00:00Z",
              updatedAt: "2026-09-09T00:00:01Z",
            },
          ],
          nextCursor: null,
        });
      }
      if (request.url.endsWith("/conversation-1/events")) {
        return Response.json({ items: [] });
      }
      if (request.url.endsWith("/conversation-1/stream")) {
        return new Response("", {
          headers: { "content-type": "text/event-stream" },
        });
      }
      if (request.url.endsWith("/conversation-1")) {
        return Response.json({
          conversationId: "conversation-1",
          title: "No evidence",
          createdAt: "2026-09-09T00:00:00Z",
          updatedAt: "2026-09-09T00:00:01Z",
          turns: [
            {
              turnId: "turn-1",
              state: "completed",
              attempt: 1,
              inputSnapshotDigest: `sha256:${"1".repeat(64)}`,
              answerEvidenceSnapshotId: "answer-1",
              answerEvidenceSnapshotDigest: `sha256:${"2".repeat(64)}`,
              input: {
                message: "No evidence",
                modelAlias: "tapper-chat",
                sourceRevisionIds: [],
                documentRevisionIds: [],
                resolvedResources: [],
                agentRevisionId: null,
                agentLabel: null,
                skillRevisionIds: [],
                skillLabels: [],
              },
            },
          ],
        });
      }
      return Response.json({ items: [] });
    });

    renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Answer evidence is unavailable",
    );
    expect(
      screen.queryByText(/This prototype response says/u),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("renders a canceled durable Turn as stopped even when partial evidence arrived", async () => {
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(input);
      if (/\/ai\/(agents|skills)$/u.test(request.url))
        return Response.json({ items: [] });
      if (request.url.endsWith("/conversations?limit=20")) {
        return Response.json({
          items: [
            {
              conversationId: "conversation-1",
              title: "Canceled prompt",
              createdAt: "2026-09-09T00:00:00Z",
              updatedAt: "2026-09-09T00:00:01Z",
            },
          ],
          nextCursor: null,
        });
      }
      if (request.url.endsWith("/conversation-1/events")) {
        return Response.json({
          items: [
            {
              eventId: "event-1",
              sequence: 1,
              turnId: "turn-1",
              eventType: "citation.resolved",
              payload: {
                citation: {
                  citationId: "citation-1",
                  sourceId: "source-1",
                  documentId: "document-1",
                  revisionId: "revision-1",
                },
              },
              occurredAt: "2026-09-09T00:00:01Z",
            },
          ],
        });
      }
      if (request.url.endsWith("/conversation-1")) {
        return Response.json({
          conversationId: "conversation-1",
          title: "Canceled prompt",
          createdAt: "2026-09-09T00:00:00Z",
          updatedAt: "2026-09-09T00:00:01Z",
          turns: [
            {
              turnId: "turn-1",
              state: "canceled",
              attempt: 1,
              inputSnapshotDigest: `sha256:${"1".repeat(64)}`,
              answerEvidenceSnapshotId: null,
              answerEvidenceSnapshotDigest: null,
              input: {
                message: "Canceled prompt",
                modelAlias: "tapper-chat",
                sourceRevisionIds: [],
                documentRevisionIds: [],
                resolvedResources: [],
                agentRevisionId: null,
                agentLabel: null,
                skillRevisionIds: [],
                skillLabels: [],
              },
            },
          ],
        });
      }
      return new Response("", {
        headers: { "content-type": "text/event-stream" },
      });
    });

    renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });

    expect(await screen.findByText("Generation stopped.")).toBeVisible();
    expect(
      screen.queryByText("回答格式无法核验，请重新提问。"),
    ).not.toBeInTheDocument();
  });

  it("keeps ready validation details out of the primary workspace", async () => {
    const user = userEvent.setup();
    renderTapAiWithTestPlans(fakeKnowledgeClient());
    expect(
      screen.queryByRole("status", { name: "Validation Mode" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(
      screen.queryByRole("status", { name: "Validation Mode" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    expect(
      screen.queryByRole("status", { name: "Validation Mode" }),
    ).not.toBeInTheDocument();
  });

  it("shows TAP AI and Tapper workspace identities", () => {
    renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });

    expect(screen.getByLabelText("TAP AI")).toHaveTextContent(/^TAP AI$/);
    const entry = screen.getByRole("button", { name: "Tapper" });
    expect(
      entry.querySelector('img[src*="tapper-owl-avatar-color.svg"]'),
    ).not.toBeNull();
    const heading = screen.getByRole("heading", { name: "Tapper" });
    expect(
      heading.querySelector('img[src*="tapper-owl-avatar-color.svg"]'),
    ).toBeNull();
    expect(
      heading.querySelector('img[src*="tapper-wordmark-ink.svg"]'),
    ).not.toBeNull();
  });

  it("uses the integrated Tapper navigation and keeps sources inside Tapper", async () => {
    renderKnowledgeApp(<TapAiPage />, { api: withKnowledgeSources() });

    const navigation = screen.getByRole("navigation", { name: "Product" });
    expect(
      within(navigation)
        .getAllByRole("button")
        .map((item) => item.getAttribute("aria-label")),
    ).toEqual(["Tapper", "Test Management"]);
    expect(
      within(screen.getByRole("navigation", { name: "Tapper tools" }))
        .getAllByRole("button")
        .map((item) => item.textContent?.trim()),
    ).toEqual(["New chat", "Agents", "Skills", "Library"]);
    expect(
      screen.getByRole("heading", { name: "What can I do for you?" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Knowledge sources" }),
    ).toBeVisible();
    expect(await screen.findByText("life-underwriting-rules.md")).toBeVisible();
    expect(screen.getByText("health-disclosure-guide.pdf")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Manage knowledge" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Intelligence Lab")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "问答" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the active Conversation across modules and remounts", async () => {
    stubDraftConversation();
    const api = fakeKnowledgeClient();
    const user = userEvent.setup();
    const message = "Create a browser automation for policy submission";

    const firstRender = renderTapAiWithTestPlans(api);
    await sendMessage(user, message);

    const history = screen.getByRole("navigation", { name: "Chat history" });
    expect(
      within(history).getByRole("button", { name: `${message}` }),
    ).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Agents" }));
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${message}` }),
    ).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Tapper" }));
    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${message}` }),
    ).toHaveAttribute("aria-current", "page");

    firstRender.unmount();
    renderKnowledgeApp(<TapAiPage />, { api });
    expect(
      await screen.findByText(message, { selector: ".tap-user-message" }),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("navigation", { name: "Chat history" }),
        ).getByRole("button", { name: `${message}` }),
      ).toHaveAttribute("aria-current", "page"),
    );
  });

  it("does not treat a workflow question as an automation request", async () => {
    stubDraftConversation();
    const user = userEvent.setup();
    renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });

    await sendMessage(user, "What is the life underwriting workflow?");

    expect(
      screen.getByText("What is the life underwriting workflow?", {
        selector: ".tap-user-message",
      }),
    ).toBeVisible();
    expect(
      screen.queryByRole("article", { name: "Generated automation" }),
    ).not.toBeInTheDocument();
  });

  it("keeps ordinary questions in the chat conversation", async () => {
    stubImmediatelyAnsweredConversation();
    const user = userEvent.setup();
    renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });

    await user.click(screen.getByRole("button", { name: "中文" }));
    await user.type(
      screen.getByRole("textbox", { name: "向 Tapper 发送消息" }),
      "寿险投保需要什么资料？",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(
      screen.getByText("寿险投保需要什么资料？", {
        selector: ".tap-user-message",
      }),
    ).toBeVisible();
    expect(
      screen.getByText(/此轮对话未选择知识上下文。回答仅基于当前可用信息/),
    ).toBeVisible();
    expect(screen.getByRole("region", { name: "Tapper 助手" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Import to Test Plan" }),
    ).not.toBeInTheDocument();
  });

  it("answers an English question in English by default", async () => {
    stubImmediatelyAnsweredConversation();
    const user = userEvent.setup();
    renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });

    await sendMessage(
      user,
      "What information is needed for a life insurance application?",
    );

    expect(
      screen.getByText(/No knowledge context was selected for this turn/),
    ).toBeVisible();
    expect(screen.queryByText(/此轮对话未选择知识上下文/)).toBeNull();
  });

  it("localizes product workspaces without losing saved conversation data", async () => {
    stubDraftConversation();
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withDocuments([
      document({
        documentId: "life-underwriting-rules",
        filename: "life-underwriting-rules.md",
        status: "ready",
        stage: "ready",
      }),
      document({
        documentId: "health-disclosure-guide",
        filename: "health-disclosure-guide.pdf",
        status: "ready",
        stage: "ready",
      }),
    ]);
    renderTapAiWithTestPlans(api);
    const prompt = "What evidence is needed for life underwriting?";

    await sendMessage(user, prompt);
    await user.click(screen.getByRole("button", { name: "中文" }));

    expect(
      screen.getByText(prompt, { selector: ".tap-user-message" }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "知识库" }));
    await user.click(screen.getByRole("tab", { name: "文档列表" }));
    expect(screen.getAllByText("知识来源 · 已就绪")).toHaveLength(2);

    // The original fixture-mode case asserted a "Test plans are available
    // when the TAP AI API is ready." status here; that placeholder text only
    // renders when TapProductPrototype is NOT in durable/api mode. In api
    // mode with a project, Test Management instead mounts the real (and
    // localized) TestPlanLibrary component, so assert its heading instead.
    // This still exercises the same behavior as the original: a locale
    // switch plus a round trip through a real-mounting Test Management must
    // not lose the saved Conversation.
    await user.click(screen.getByRole("button", { name: "测试管理" }));
    expect(await screen.findByRole("heading", { name: "测试管理" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Tapper" }));
    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(
      within(screen.getByRole("navigation", { name: "对话历史" })).getByRole(
        "button",
        {
          name: /^What evidence is needed for life underwriting\?/,
        },
      ),
    );
    expect(
      screen.getByText(prompt, { selector: ".tap-user-message" }),
    ).toBeVisible();
  });

  it("moves the composer from the centered start state to the conversation dock", async () => {
    stubDraftConversation();
    const user = userEvent.setup();
    renderKnowledgeApp(<TapAiPage />, { api: fakeKnowledgeClient() });

    const start = screen.getByRole("region", { name: "Start a conversation" });
    expect(
      within(start).getByRole("heading", { name: "What can I do for you?" }),
    ).toBeVisible();
    expect(
      within(start).getByRole("form", { name: "Message composer" }),
    ).toBeVisible();

    const composer = within(start).getByRole("textbox", {
      name: "Message Tapper",
    });
    await user.type(composer, "寿险投保需要什么资料？");
    await user.keyboard("{Enter}");

    expect(
      screen.queryByRole("region", { name: "Start a conversation" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("log", { name: "Conversation" })).toBeVisible();
    expect(
      screen.getByRole("form", { name: "Message composer" }),
    ).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "Message Tapper" }),
    ).toHaveFocus();
  });
});
