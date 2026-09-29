import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { PrototypeSidebar } from "./PrototypeSidebar";
import { PROTOTYPE_COPY } from "./copy";
import { createConversation, type Conversation } from "./model";
import { setPrototypeFaults } from "./prototypeFaults";
import { TapProductPrototype } from "../TapProductPrototype";
import {
  PROTOTYPE_STORAGE_KEY,
  PROTOTYPE_SNAPSHOT_VERSION,
} from "./artifacts/persistence";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  setPrototypeFaults([]);
});

function conversationWithTurn(id: string, title: string): Conversation {
  return {
    ...createConversation(id, { title }),
    turns: [
      {
        id: `${id}-turn`,
        intent: "answer",
        locale: "en",
        modelId: "gpt-5.6-sol",
        prompt: title,
        sourceReferences: [],
        answerState: "completed",
        trace: { searchedSources: 1, matchedPassages: 1, citations: 1 },
      },
    ],
  };
}

function Harness({
  initialConversations,
  onDeleteConversation,
}: {
  initialConversations: readonly Conversation[];
  onDeleteConversation?: (conversationId: string) => boolean;
}) {
  const [conversations, setConversations] = useState(initialConversations);

  return (
    <PrototypeSidebar
      activeConversationId="active-chat"
      activeModule="tapper"
      collapsed={false}
      conversations={conversations}
      copy={PROTOTYPE_COPY.en}
      locale="en"
      onLocaleChange={() => {}}
      onModuleChange={() => {}}
      onNewChat={() => {}}
      onDeleteConversation={
        onDeleteConversation === undefined
          ? undefined
          : (conversationId: string) => {
              const deleted = onDeleteConversation(conversationId);
              if (deleted) {
                setConversations((current) =>
                  current.filter(({ id }) => id !== conversationId),
                );
              }
              return deleted;
            }
      }
      onRenameConversation={() => {}}
      onSelectConversation={() => {}}
      onToggleCollapsed={() => {}}
    />
  );
}

it("pages chat history", () => {
  const conversations = Array.from({ length: 13 }, (_, index) =>
    conversationWithTurn(`chat-${index + 1}`, `Sample question ${index + 1}`),
  );
  render(<Harness initialConversations={conversations} />);

  expect(
    within(screen.getByRole("navigation", { name: "Chat history" })).getAllByRole(
      "button",
      { name: /Sample question \d+/ },
    ),
  ).toHaveLength(10);
  expect(screen.getByRole("button", { name: "Load more" })).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Load more" }));

  expect(
    within(screen.getByRole("navigation", { name: "Chat history" })).getAllByRole(
      "button",
      { name: /Sample question \d+/ },
    ),
  ).toHaveLength(13);
  expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
});

it("retries a failed history load", () => {
  setPrototypeFaults(["history-load-failed"]);
  const conversations = [conversationWithTurn("chat-1", "A question")];
  render(<Harness initialConversations={conversations} />);

  expect(
    screen.getByText("Chat history could not be loaded."),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "A question" }),
  ).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(
    screen.queryByText("Chat history could not be loaded."),
  ).toBeNull();
  expect(screen.getByRole("button", { name: "A question" })).toBeVisible();
});

it("keeps the chat when deletion fails", () => {
  const conversations = [conversationWithTurn("chat-1", "A question")];
  render(
    <Harness
      initialConversations={conversations}
      onDeleteConversation={() => false}
    />,
  );

  fireEvent.click(
    screen.getByRole("button", { name: "More options for A question" }),
  );
  fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
  fireEvent.click(
    within(screen.getByRole("dialog", { name: "Delete chat?" })).getByRole(
      "button",
      { name: "Delete" },
    ),
  );

  expect(screen.queryByRole("dialog", { name: "Delete chat?" })).toBeNull();
  expect(screen.getByRole("button", { name: "A question" })).toBeVisible();
  expect(
    screen.getByText("The chat could not be deleted. Please try again."),
  ).toBeVisible();
});

it("loads a snapshot without seeded history fields", () => {
  localStorage.setItem(
    PROTOTYPE_STORAGE_KEY,
    JSON.stringify({
      version: PROTOTYPE_SNAPSHOT_VERSION,
      activeConversationId: "chat-1",
      conversations: [createConversation("chat-1")],
      artifacts: { automations: [], testPlans: [], runs: [] },
    }),
  );

  render(<TapProductPrototype />);

  expect(
    screen.getByRole("heading", { name: "What can I do for you?" }),
  ).toBeVisible();
  expect(screen.queryByRole("navigation", { name: "Chat history" })).toBeNull();
});
