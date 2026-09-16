import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  document,
  fakeKnowledgeClient,
} from "../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../features/knowledge/testing/renderKnowledgeApp";
import { TapperPage } from "./TapperPage";
import { TapProductPrototype } from "../widgets/tap/TapProductPrototype";

function renderPrototype() {
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
  return renderKnowledgeApp(
    <TapProductPrototype conversationSource="fixture" />,
    { api },
  );
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

describe("Tapper product prototype", () => {
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

    const first = renderKnowledgeApp(<TapperPage />, {
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
      ).getByRole("button", { name: /Older durable prompt/u }),
    ).toBeVisible();
    first.unmount();
    renderKnowledgeApp(<TapperPage />, { api: fakeKnowledgeClient() });
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

    renderKnowledgeApp(<TapperPage />, { api: fakeKnowledgeClient() });

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

    renderKnowledgeApp(<TapperPage />, { api: fakeKnowledgeClient() });

    expect(await screen.findByText("Generation stopped.")).toBeVisible();
    expect(
      screen.queryByText("回答格式无法核验，请重新提问。"),
    ).not.toBeInTheDocument();
  });

  it("keeps ready validation details out of the primary workspace", async () => {
    const user = userEvent.setup();
    renderPrototype();
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

  it("shows Tap AI and Tapper workspace identities", () => {
    renderKnowledgeApp(<TapperPage />, { api: fakeKnowledgeClient() });

    expect(screen.getByLabelText("Tap AI")).toHaveTextContent(/^Tap AI$/);
    const entry = screen.getByRole("button", { name: "Tapper" });
    expect(
      entry.querySelector('img[src*="tapper-listening-avatar-color.svg"]'),
    ).not.toBeNull();
    const heading = screen.getByRole("heading", { name: "Tapper" });
    expect(
      heading.querySelector('img[src*="tapper-listening-avatar-color.svg"]'),
    ).toBeNull();
    expect(
      heading.querySelector('img[src*="tapper-wordmark-ink.svg"]'),
    ).not.toBeNull();
  });

  it("uses the integrated Tapper navigation and keeps sources inside Tapper", async () => {
    renderPrototype();

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
    expect(
      screen.getByRole("button", {
        name: "Create BDD test cases for life insurance underwriting",
      }),
    ).toBeVisible();
    expect(screen.queryByText("Intelligence Lab")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "问答" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the explicit demo fixture Conversation across modules and remounts", async () => {
    const user = userEvent.setup();
    const firstRender = renderPrototype();
    const message = "Create a browser automation for policy submission";

    await sendMessage(user, message);
    const history = screen.getByRole("navigation", { name: "Chat history" });
    expect(
      within(history).getByRole("button", {
        name: `${message}`,
      }),
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
    renderPrototype();
    expect(
      screen.getByText(message, { selector: ".tap-user-message" }),
    ).toBeVisible();
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${message}` }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("does not treat a workflow question as an automation request", async () => {
    const user = userEvent.setup();
    renderPrototype();

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
    const user = userEvent.setup();
    renderPrototype();

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
    const user = userEvent.setup();
    renderPrototype();

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
    const user = userEvent.setup();
    renderPrototype();
    const prompt = "What evidence is needed for life underwriting?";

    await sendMessage(user, prompt);
    await user.click(screen.getByRole("button", { name: "中文" }));

    expect(
      screen.getByText(prompt, { selector: ".tap-user-message" }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "知识库" }));
    await user.click(screen.getByRole("tab", { name: "文档列表" }));
    expect(screen.getAllByText("知识来源 · 已就绪")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "测试管理" }));
    expect(screen.getByRole("status")).toHaveTextContent(
      "Test plans are available when the Tap AI API is ready.",
    );

    await user.click(screen.getByRole("button", { name: "Tapper" }));
    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(
      within(screen.getByRole("navigation", { name: "对话历史" })).getByRole(
        "button",
        {
          name: /What evidence is needed for life underwriting\?/,
        },
      ),
    );
    expect(
      screen.getByText(prompt, { selector: ".tap-user-message" }),
    ).toBeVisible();
  });

  it("moves the composer from the centered start state to the conversation dock", async () => {
    const user = userEvent.setup();
    renderPrototype();

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
