import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { fakeKnowledgeClient } from "../../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../../features/knowledge/testing/renderKnowledgeApp";
import { TapProductPrototype } from "./TapProductPrototype";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });

const PUBLISHED = {
  items: [
    {
      sourceId: "src_policy",
      sourceName: "Policy source",
      documentId: "doc_policy",
      filename: "policy.md",
      revisionId: "rev_policy",
      publicationId: "pub_policy",
      approvedItemCount: 1,
      inventoryItemCount: 1,
      partial: false,
      expiresAt: "2027-01-01T00:00:00Z",
    },
  ],
};

const ANSWER = {
  traceId: "trace-1",
  queryPlanId: "plan-1",
  contextSnapshotId: "context-1",
  corpusVersion: "v1",
  retrievalProfileId: "quick",
  degradedMode: false,
  answer: "Verified identity is required.",
  abstained: false,
  claims: [
    {
      claimId: "claim-1",
      text: "Verified identity is required.",
      citationIds: [],
    },
  ],
  citations: [],
};

interface Scenario {
  state: "completed" | "failed" | "canceled" | "running";
  appendResponse?: () => Promise<Response>;
}

function durableConversation({ state, appendResponse }: Scenario) {
  const appended: Request[] = [];
  const canceled: string[] = [];
  const renamed: unknown[] = [];
  const deleted: string[] = [];
  const searches: string[] = [];
  let title = "What is the rule?";
  vi.stubGlobal("fetch", async (request: Request) => {
    const url = new URL(request.url);
    const path = url.pathname;
    if (path.endsWith("/conversations") && request.method === "GET") {
      const q = url.searchParams.get("q");
      if (q !== null) searches.push(q);
      const all = [
        {
          conversationId: "conversation-a",
          title,
          createdAt: "2026-09-29T08:00:00Z",
          updatedAt: "2026-09-29T08:00:00Z",
        },
        {
          conversationId: "conversation-b",
          title: "Beneficiary change steps",
          createdAt: "2026-09-28T08:00:00Z",
          updatedAt: "2026-09-28T08:00:00Z",
        },
      ].filter(
        (item) =>
          !deleted.includes(item.conversationId) &&
          (q === null || item.title.toLowerCase().includes(q.toLowerCase())),
      );
      return json({ items: all, nextCursor: null });
    }
    if (
      path.endsWith("/conversations/conversation-a") &&
      request.method === "PATCH"
    ) {
      const body = (await request.json()) as { title: string };
      renamed.push(body);
      title = body.title;
      return json({
        conversationId: "conversation-a",
        title,
        createdAt: "2026-09-29T08:00:00Z",
        updatedAt: "2026-09-29T09:00:00Z",
      });
    }
    if (request.method === "DELETE" && path.includes("/conversations/")) {
      deleted.push(path.split("/").pop()!);
      return new Response(null, { status: 204 });
    }
    if (path.endsWith("/conversations/conversation-a"))
      return json({
        conversationId: "conversation-a",
        title: "What is the rule?",
        createdAt: "2026-09-29T08:00:00Z",
        updatedAt: "2026-09-29T08:00:00Z",
        turns: [
          {
            turnId: "turn-1",
            state,
            attempt: 1,
            inputSnapshotDigest: `sha256:${"a".repeat(64)}`,
            answerEvidenceSnapshotDigest: null,
            input: {
              message: "What is the rule?",
              modelAlias: "tapper-chat",
              sourceRevisionIds: ["rev_policy"],
              documentRevisionIds: [],
              resolvedResources: [
                {
                  sourceId: "src_policy",
                  documentId: "doc_policy",
                  sourceRevisionId: "rev_policy",
                  documentRevisionId: "rev_policy",
                  label: "Policy source",
                },
              ],
              agentRevisionId: null,
              agentLabel: null,
              skillRevisionIds: [],
              skillLabels: [],
              insightsQueryId: null,
            },
          },
        ],
      });
    if (path.endsWith("/conversations/conversation-a/events"))
      return json({
        items:
          state === "completed"
            ? [
                {
                  eventId: "event-1",
                  sequence: 1,
                  turnId: "turn-1",
                  occurredAt: "2026-09-29T08:00:01Z",
                  eventType: "turn.completed",
                  payload: { answer: ANSWER },
                },
              ]
            : [],
        nextCursor: null,
      });
    if (path.endsWith("/conversations/conversation-a/turns")) {
      appended.push(request);
      if (appendResponse !== undefined) return appendResponse();
      return json(
        { conversationId: "conversation-a", turnId: "turn-2", state: "queued" },
        202,
      );
    }
    if (path.endsWith("/turns/turn-1/cancel")) {
      canceled.push(path);
      return json(
        {
          conversationId: "conversation-a",
          turnId: "turn-1",
          state: "canceled",
        },
        202,
      );
    }
    throw new Error(`Unexpected API call: ${request.method} ${request.url}`);
  });
  const api = fakeKnowledgeClient().withPublishedSources(PUBLISHED);
  const rendered = renderKnowledgeApp(
    <TapProductPrototype conversationSource="api" />,
    { api },
  );
  return { api, appended, canceled, renamed, deleted, searches, ...rendered };
}

it("regenerates a completed answer as a new turn with the same question and sources", async () => {
  const { appended } = durableConversation({ state: "completed" });
  const user = userEvent.setup();

  await user.click(await screen.findByRole("button", { name: "Regenerate" }));

  await waitFor(() => expect(appended).toHaveLength(1));
  expect(await appended[0]!.json()).toMatchObject({
    message: "What is the rule?",
    sourceRevisionIds: ["rev_policy"],
  });
});

it.each(["failed", "canceled"] as const)(
  "retries a %s turn by sending the same question again",
  async (state) => {
    const { appended } = durableConversation({ state });
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Retry" }));

    await waitFor(() => expect(appended).toHaveLength(1));
    expect(await appended[0]!.json()).toMatchObject({
      message: "What is the rule?",
      sourceRevisionIds: ["rev_policy"],
    });
  },
);

it("replaces Send with Stop generating while a reply is running", async () => {
  const { canceled } = durableConversation({ state: "running" });
  const user = userEvent.setup();

  const stop = await screen.findByRole("button", { name: "Stop generating" });
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  await user.click(stop);

  await waitFor(() => expect(canceled).toHaveLength(1));
});

it("shows a busy Send button until the message is accepted", async () => {
  let accept: (() => void) | undefined;
  durableConversation({
    state: "completed",
    appendResponse: () =>
      new Promise<Response>((resolve) => {
        accept = () =>
          resolve(
            json(
              {
                conversationId: "conversation-a",
                turnId: "turn-2",
                state: "queued",
              },
              202,
            ),
          );
      }),
  });
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Regenerate" });

  await user.type(
    screen.getByRole("textbox", { name: "Message Tapper" }),
    "Next question",
  );
  await user.click(screen.getByRole("button", { name: "Send" }));

  const send = await screen.findByRole("button", { name: "Send" });
  await waitFor(() => expect(send).toHaveAttribute("aria-busy", "true"));
  expect(send).toBeDisabled();
  accept!();
  await waitFor(() => expect(send).not.toHaveAttribute("aria-busy"));
});

it("reuses the send key after an ambiguous failure so retries do not duplicate", async () => {
  let attempts = 0;
  const { appended } = durableConversation({
    state: "completed",
    appendResponse: async () => {
      attempts += 1;
      if (attempts === 1) throw new TypeError("Response lost after acceptance");
      return json(
        { conversationId: "conversation-a", turnId: "turn-2", state: "queued" },
        202,
      );
    },
  });
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Regenerate" });
  const composer = screen.getByRole("textbox", { name: "Message Tapper" });

  await user.type(composer, "Next question");
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(await screen.findByText(/Message was not sent/u)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(appended).toHaveLength(2));
  expect(appended[1]!.headers.get("idempotency-key")).toBe(
    appended[0]!.headers.get("idempotency-key"),
  );
});

it("edits a previous question in the composer without rewriting history", async () => {
  const { appended } = durableConversation({ state: "completed" });
  const user = userEvent.setup();

  await user.click(
    await screen.findByRole("button", {
      name: "Edit question: What is the rule?",
    }),
  );

  expect(screen.getByRole("textbox", { name: "Message Tapper" })).toHaveValue(
    "What is the rule?",
  );
  expect(appended).toHaveLength(0);
});

it("uploads an attachment to the Library and uses it only once published", async () => {
  const { api, queryClient } = durableConversation({ state: "completed" });
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Regenerate" });

  await user.click(screen.getByRole("button", { name: "Add to message" }));
  await user.click(screen.getByRole("menuitem", { name: "Upload file" }));
  await user.upload(
    screen.getByLabelText("Upload file", { selector: "input" }),
    new File(["# Claims\n\nClaims need evidence."], "claims.md", {
      type: "text/markdown",
    }),
  );

  const attachments = await screen.findByRole("group", { name: "Upload file" });
  await waitFor(() =>
    expect(attachments).toHaveTextContent(
      /claims\.md.*(Processing|No searchable chunks yet)/u,
    ),
  );
  const sourceId = (await api.listSources({ limit: 50 })).items.find(
    (item) => item.name === "claims.md",
  )!.sourceId;
  api.withPublishedSources({
    items: [
      ...PUBLISHED.items,
      {
        ...PUBLISHED.items[0]!,
        sourceId,
        sourceName: "claims.md",
        documentId: "doc_claims",
        filename: "claims.md",
        revisionId: "rev_claims",
      },
    ],
  });
  await queryClient.invalidateQueries({
    queryKey: ["knowledge", "project-test", "published-sources"],
  });

  await waitFor(() =>
    expect(screen.queryByRole("group", { name: "Upload file" })).toBeNull(),
  );
  expect(
    within(screen.getByRole("group", { name: "Message context" })).getByText(
      "claims.md",
    ),
  ).toBeVisible();
});

it("renames a chat from its history row menu with the keyboard", async () => {
  const { renamed } = durableConversation({ state: "completed" });
  const user = userEvent.setup();
  const more = await screen.findByRole("button", {
    name: "More options for What is the rule?",
  });

  more.focus();
  await user.keyboard("{Enter}");
  const menu = screen.getByRole("menu", {
    name: "More options for What is the rule?",
  });
  expect(within(menu).getByRole("menuitem", { name: "Rename" })).toHaveFocus();
  await user.keyboard("{ArrowDown}");
  expect(within(menu).getByRole("menuitem", { name: "Delete" })).toHaveFocus();
  await user.keyboard("{ArrowUp}{Enter}");

  const field = screen.getByRole("textbox", { name: "Chat name" });
  await user.clear(field);
  await user.keyboard("{Enter}");
  expect(
    within(screen.getByRole("navigation", { name: "Chat history" })).getByRole(
      "alert",
    ),
  ).toHaveTextContent("Enter a name of 1–120 characters.");
  await user.type(field, "  Identity rules  {Enter}");

  await waitFor(() => expect(renamed).toEqual([{ title: "Identity rules" }]));
  expect(
    await screen.findByRole("button", { name: /^Identity rules/u }),
  ).toBeVisible();
});

it("cancels a rename with Escape and keeps the title", async () => {
  const { renamed } = durableConversation({ state: "completed" });
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", {
      name: "More options for What is the rule?",
    }),
  );
  await user.click(screen.getByRole("menuitem", { name: "Rename" }));
  await user.type(screen.getByRole("textbox", { name: "Chat name" }), "X");
  await user.keyboard("{Escape}");

  expect(screen.queryByRole("textbox", { name: "Chat name" })).toBeNull();
  expect(renamed).toEqual([]);
});

it("deletes the open chat after confirmation and starts a new chat", async () => {
  const { deleted } = durableConversation({ state: "completed" });
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Regenerate" });

  await user.click(
    screen.getByRole("button", { name: "More options for What is the rule?" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "Delete" }));
  const confirm = screen.getByRole("alertdialog", { name: "Delete chat?" });
  await user.click(within(confirm).getByRole("button", { name: "Delete" }));

  await waitFor(() => expect(deleted).toEqual(["conversation-a"]));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Regenerate" })).toBeNull(),
  );
  expect(
    screen.queryByRole("button", { name: /^What is the rule\?/u }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: /^Beneficiary change steps/u }),
  ).toBeVisible();
});

it("searches chat history by title on the server", async () => {
  const { searches } = durableConversation({ state: "completed" });
  const user = userEvent.setup();
  const nav = await screen.findByRole("navigation", { name: "Chat history" });

  await user.type(
    within(nav).getByRole("searchbox", { name: "Search chats" }),
    "benef",
  );

  await waitFor(() => expect(searches).toContain("benef"));
  await waitFor(() =>
    expect(
      within(nav).queryByRole("button", { name: /^What is the rule\?/u }),
    ).toBeNull(),
  );
  expect(
    within(nav).getByRole("button", { name: "Beneficiary change steps" }),
  ).toBeVisible();

  await user.clear(
    within(nav).getByRole("searchbox", { name: "Search chats" }),
  );
  await user.type(
    within(nav).getByRole("searchbox", { name: "Search chats" }),
    "nothing",
  );
  expect(await within(nav).findByText("No matching chats")).toBeVisible();
});
