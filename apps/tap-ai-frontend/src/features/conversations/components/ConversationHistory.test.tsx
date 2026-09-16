import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderApp } from "../../../shared/testing/renderApp";
import { ConversationHistory } from "./ConversationHistory";

describe("ConversationHistory", () => {
  it("shows loading, error with recovery, and an actionable empty state", async () => {
    const retry = vi.fn();
    const view = renderApp(
      <ConversationHistory
        activeId={null}
        conversations={[]}
        isLoading
        onLoadMore={() => undefined}
        onRetry={retry}
        onSelect={() => undefined}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading conversations",
    );

    view.rerender(
      <ConversationHistory
        activeId={null}
        conversations={[]}
        error="Conversation history is unavailable."
        onLoadMore={() => undefined}
        onRetry={retry}
        onSelect={() => undefined}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();

    view.rerender(
      <ConversationHistory
        activeId={null}
        conversations={[]}
        onLoadMore={() => undefined}
        onRetry={retry}
        onSelect={() => undefined}
      />,
    );
    expect(
      screen.getByText("Your conversations will appear here."),
    ).toBeVisible();
  });

  it("selects a durable conversation and loads the next page", async () => {
    const select = vi.fn();
    const loadMore = vi.fn();
    renderApp(
      <ConversationHistory
        activeId="conversation-2"
        conversations={[
          {
            conversationId: "conversation-1",
            title: "A very long conversation title that remains readable",
            createdAt: "2026-09-09T00:00:00Z",
            updatedAt: "2026-09-09T00:00:00Z",
          },
          {
            conversationId: "conversation-2",
            title: "Second question",
            createdAt: "2026-09-09T00:01:00Z",
            updatedAt: "2026-09-09T00:01:00Z",
          },
        ]}
        hasMore
        onLoadMore={loadMore}
        onRetry={() => undefined}
        onSelect={select}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Second question" }),
    ).toHaveAttribute("aria-current", "page");
    await userEvent.click(
      screen.getByRole("button", {
        name: "A very long conversation title that remains readable",
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(select).toHaveBeenCalledWith("conversation-1");
    expect(loadMore).toHaveBeenCalledOnce();
  });
});
