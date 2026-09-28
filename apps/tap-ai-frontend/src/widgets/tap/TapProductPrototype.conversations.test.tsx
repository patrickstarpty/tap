import { screen, waitFor } from "@testing-library/react";
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

it("keeps an explicit New chat when durable history arrives afterwards", async () => {
  let releaseList: (() => void) | undefined;
  const requested: string[] = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    requested.push(new URL(request.url).pathname);
    if (request.url.endsWith("/conversations?limit=20")) {
      await new Promise<void>((resolve) => {
        releaseList = resolve;
      });
      return json({
        items: [
          {
            conversationId: "conversation-old",
            title: "Earlier question",
            createdAt: "2026-09-28T08:00:00Z",
            updatedAt: "2026-09-28T08:00:00Z",
          },
        ],
        nextCursor: null,
      });
    }
    throw new Error(`Unexpected API call: ${request.url}`);
  });
  renderKnowledgeApp(<TapProductPrototype conversationSource="api" />, {
    api: fakeKnowledgeClient(),
  });
  const user = userEvent.setup();

  await waitFor(() => expect(releaseList).toBeDefined());
  await user.click(screen.getByRole("button", { name: "New chat" }));
  releaseList!();

  expect(
    await screen.findByRole("button", { name: /Earlier question/u }),
  ).toBeInTheDocument();
  expect(
    requested.some((path) => path.endsWith("/conversations/conversation-old")),
  ).toBe(false);
  expect(screen.getByRole("textbox", { name: "Message Tapper" })).toHaveValue(
    "",
  );
});
