import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { fakeKnowledgeClient } from "../../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../../features/knowledge/testing/renderKnowledgeApp";
import { TapProductPrototype } from "./TapProductPrototype";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  window.sessionStorage.removeItem("tap:insights-pending:project-test");
});

it("sends a report handoff with its query and receipts, then shows verified Insights facts", async () => {
  window.history.replaceState(
    null,
    "",
    "/?projectId=project-test&queryId=query-a&resourceRef=receipt-a&draft=Explain+this+failure",
  );
  const sent: Request[] = [];
  let denied = false;
  let releaseDenial: (() => void) | undefined;
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.url.endsWith("/insights/explanations")) {
      sent.push(request);
      return new Response(
        JSON.stringify({
          conversationId: "conversation-a",
          turnId: "turn-a",
          state: "queued",
        }),
        { status: 202, headers: { "content-type": "application/json" } },
      );
    }
    if (
      request.url.endsWith("/insights/explanations/conversation-a/turns/turn-a")
    ) {
      if (denied) {
        await new Promise<void>((resolve) => {
          releaseDenial = resolve;
        });
        return new Response("", { status: 403 });
      }
      return new Response(
        JSON.stringify({
          queryId: "query-a",
          metricVersion: "insights-metrics-v1",
          asOf: "2026-09-25T08:00:00Z",
          facts: [
            {
              metricId: "first_pass_rate",
              numerator: 1,
              denominator: 2,
              value: 0.5,
              completeness: "complete",
              missingReasons: [],
              evidenceRefs: ["receipt-a"],
            },
          ],
          reportCoverage: [],
          hypotheses: [
            "Request timeout may be associated with retry recovery. [receipt-a]",
            "Permission mismatch may be associated with access failure. [receipt-b]",
          ],
          evidenceExcerpts: [
            { citationId: "receipt-a", text: "Assertion failed" },
            { citationId: "receipt-b", text: "Permission denied" },
          ],
          missingInformation: [
            "Please provide HTTP 403 logs.",
            "Permission change history is needed.",
          ],
          stopReason: "completed",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    if (request.url.endsWith("/conversations?limit=20"))
      return new Response(
        JSON.stringify({
          items:
            sent.length === 0
              ? []
              : [
                  {
                    conversationId: "conversation-a",
                    title: "Explain this failure",
                    createdAt: "2026-09-25T08:00:00Z",
                    updatedAt: "2026-09-25T08:00:00Z",
                  },
                ],
          nextCursor: null,
        }),
        {
          status: 200,
        },
      );
    if (request.url.endsWith("/conversations/conversation-a"))
      return new Response(
        JSON.stringify({
          conversationId: "conversation-a",
          title: "Explain this failure",
          createdAt: "2026-09-25T08:00:00Z",
          updatedAt: "2026-09-25T08:00:00Z",
          turns: [
            {
              turnId: "turn-a",
              state: "completed",
              attempt: 1,
              inputSnapshotDigest: `sha256:${"a".repeat(64)}`,
              answerEvidenceSnapshotDigest: null,
              input: {
                message: "Explain this failure",
                modelAlias: "qwen-plus",
                sourceRevisionIds: ["rev_policy"],
                documentRevisionIds: [],
                resolvedResources: [],
                agentRevisionId: null,
                agentLabel: null,
                skillRevisionIds: [],
                skillLabels: [],
                insightsQueryId: "query-a",
              },
            },
          ],
        }),
        { status: 200 },
      );
    if (request.url.endsWith("/conversations/conversation-a/events"))
      return new Response(JSON.stringify({ items: [], nextCursor: null }), {
        status: 200,
      });
    throw new Error(`Unexpected API call: ${request.url}`);
  });
  const api = fakeKnowledgeClient().withPublishedSources({
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
  });
  const { queryClient, unmount } = renderKnowledgeApp(
    <TapProductPrototype conversationSource="api" />,
    {
      api,
    },
  );
  const user = userEvent.setup();
  await user.click(
    await within(
      screen.getByRole("complementary", { name: "Knowledge sources" }),
    ).findByRole("checkbox", { name: /Policy source/u }),
  );

  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: "Message Tapper" })).toHaveValue(
      "Explain this failure",
    ),
  );
  await user.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(sent).toHaveLength(1));
  expect(await sent[0]!.json()).toEqual({
    queryId: "query-a",
    resourceRefs: ["receipt-a"],
    question: "Explain this failure",
    sourceRevisionIds: ["rev_policy"],
    documentRevisionIds: [],
  });
  expect(sent[0]!.headers.get("idempotency-key")).toBeTruthy();
  expect(
    await screen.findByRole("region", { name: "Insights explanation" }),
  ).toHaveTextContent("50% (1/2)");
  expect(
    screen.getByRole("region", { name: "Insights explanation" }),
  ).toHaveTextContent("receipt-a");
  expect(
    screen.getByRole("region", { name: "Insights explanation" }),
  ).toHaveTextContent("Assertion failed");
  expect(screen.getByText(/Request timeout may/)).toBeVisible();
  expect(screen.getByText(/Permission mismatch may/)).toBeVisible();
  expect(screen.getByText("Please provide HTTP 403 logs.")).toBeVisible();
  expect(
    screen.getByText("Permission change history is needed."),
  ).toBeVisible();
  await queryClient.invalidateQueries({
    queryKey: ["conversations", "project-test"],
  });
  expect(
    screen.getByRole("region", { name: "Insights explanation" }),
  ).toHaveTextContent("Assertion failed");
  unmount();
  const restored = renderKnowledgeApp(
    <TapProductPrototype conversationSource="api" />,
    { api },
  );
  expect(await screen.findByText(/Request timeout may/)).toBeVisible();
  expect(screen.getByText(/Permission mismatch may/)).toBeVisible();
  expect(screen.getByText("Please provide HTTP 403 logs.")).toBeVisible();
  expect(sent).toHaveLength(1);
  // Keep the successful query cached while reopening after authorization changes.
  restored.unmount();
  denied = true;
  renderKnowledgeApp(<TapProductPrototype conversationSource="api" />, {
    api,
    queryClient: restored.queryClient,
  });
  await waitFor(() => expect(releaseDenial).toBeDefined());
  expect(
    screen.queryByRole("region", { name: "Insights explanation" }),
  ).toBeNull();
  releaseDenial!();
  expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
  expect(
    screen.queryByRole("region", { name: "Insights explanation" }),
  ).toBeNull();
  expect(screen.queryByText(/Request timeout may/)).toBeNull();
  expect(screen.queryByText("Assertion failed")).toBeNull();
});

it("keeps the report question available when explanation authorization fails", async () => {
  window.history.replaceState(
    null,
    "",
    "/?projectId=project-test&queryId=query-a&resourceRef=receipt-a&draft=Explain+this+failure",
  );
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.url.endsWith("/insights/explanations"))
      return new Response("", { status: 403 });
    if (request.url.endsWith("/conversations?limit=20"))
      return new Response(JSON.stringify({ items: [], nextCursor: null }), {
        status: 200,
      });
    throw new Error(`Unexpected API call: ${request.url}`);
  });
  renderKnowledgeApp(<TapProductPrototype conversationSource="api" />, {
    api: fakeKnowledgeClient(),
  });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Send" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("access");
  expect(screen.getByRole("textbox", { name: "Message Tapper" })).toHaveValue(
    "Explain this failure",
  );
  expect(
    screen.queryByRole("region", { name: "Insights explanation" }),
  ).toBeNull();
});

it("reuses the durable send key after an uncertain POST response", async () => {
  window.history.replaceState(
    null,
    "",
    "/?projectId=project-test&queryId=query-a&resourceRef=receipt-a&draft=Explain+this+failure",
  );
  const keys: string[] = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    if (request.url.endsWith("/insights/explanations")) {
      keys.push(request.headers.get("idempotency-key") ?? "");
      if (keys.length === 1) throw new Error("Response lost after acceptance");
      return new Response(
        JSON.stringify({
          conversationId: "conversation-a",
          turnId: "turn-a",
          state: "queued",
        }),
        {
          status: 202,
          headers: { "content-type": "application/json" },
        },
      );
    }
    if (
      request.url.endsWith("/insights/explanations/conversation-a/turns/turn-a")
    )
      return new Response(
        JSON.stringify({
          conversationId: "conversation-a",
          turnId: "turn-a",
          state: "running",
        }),
        {
          status: 202,
          headers: { "content-type": "application/json" },
        },
      );
    if (request.url.endsWith("/conversations?limit=20"))
      return new Response(JSON.stringify({ items: [], nextCursor: null }), {
        status: 200,
      });
    throw new Error(`Unexpected API call: ${request.url}`);
  });
  renderKnowledgeApp(<TapProductPrototype conversationSource="api" />, {
    api: fakeKnowledgeClient(),
  });
  const user = userEvent.setup();
  const send = await screen.findByRole("button", { name: "Send" });
  await user.click(send);
  expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
  await user.click(send);
  await waitFor(() => expect(keys).toHaveLength(2));
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
});
