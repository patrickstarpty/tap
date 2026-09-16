import type { components } from "../../../shared/api/generated/schema";
import type { ReactNode } from "react";

type ConversationSummary = components["schemas"]["ConversationSummary"];

export function ConversationHistory({
  activeId,
  conversations,
  error,
  hasMore = false,
  isLoading = false,
  isLoadingMore = false,
  getLabel = (conversation) => conversation.title,
  sectionTitle,
  ariaLabel = "Chat history",
  icon,
  onLoadMore,
  onRetry,
  onSelect,
}: {
  activeId: string | null;
  conversations: readonly ConversationSummary[];
  error?: string;
  hasMore?: boolean;
  isLoading?: boolean;
  isLoadingMore?: boolean;
  onLoadMore: () => void;
  onRetry: () => void;
  onSelect: (conversationId: string) => void;
  getLabel?: (conversation: ConversationSummary, index: number) => string;
  sectionTitle?: string;
  ariaLabel?: string;
  icon?: ReactNode;
}) {
  if (isLoading) {
    return (
      <p className="tap-chat-history-state" role="status">
        Loading conversations…
      </p>
    );
  }
  if (error !== undefined) {
    return (
      <div className="tap-chat-history-state" role="alert">
        <p>{error}</p>
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  }
  if (conversations.length === 0) {
    return (
      <p className="tap-chat-history-state">
        Your conversations will appear here.
      </p>
    );
  }
  return (
    <nav aria-label={ariaLabel} className="tap-chat-history">
      {sectionTitle === undefined ? null : (
        <span className="tap-sidebar-section-title">{sectionTitle}</span>
      )}
      {conversations.map((conversation, index) => {
        const label = getLabel(conversation, index);
        return (
          <button
            key={conversation.conversationId}
            type="button"
            aria-label={label}
            aria-current={
              conversation.conversationId === activeId ? "page" : undefined
            }
            title={label}
            onClick={() => onSelect(conversation.conversationId)}
          >
            {icon}
            <span>{label}</span>
          </button>
        );
      })}
      {hasMore ? (
        <button type="button" disabled={isLoadingMore} onClick={onLoadMore}>
          {isLoadingMore ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </nav>
  );
}
