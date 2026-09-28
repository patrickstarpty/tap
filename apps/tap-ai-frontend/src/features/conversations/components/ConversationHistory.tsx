import type { components } from "../../../shared/api/generated/schema";
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";

type ConversationSummary = components["schemas"]["ConversationSummary"];

export interface ConversationHistoryActions {
  labels: {
    search: string;
    noMatches: string;
    moreOptions: (title: string) => string;
    rename: string;
    delete: string;
    chatName: string;
    chatNameInvalid: string;
    confirmDelete: string;
    deleteWarning: string;
    cancel: string;
  };
  search: string;
  onSearchChange: (value: string) => void;
  /** Rows to show while searching; undefined keeps the unfiltered list. */
  searchResults?: readonly ConversationSummary[];
  isSearching?: boolean;
  error?: string;
  canManage?: (conversationId: string) => boolean;
  onRename: (conversationId: string, title: string) => Promise<boolean>;
  onDelete: (conversationId: string) => Promise<boolean>;
}

type Editing =
  | { kind: "rename"; conversationId: string; value: string; invalid: boolean }
  | { kind: "delete"; conversationId: string };

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
  actions,
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
  actions?: ConversationHistoryActions;
}) {
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [pending, setPending] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const renameRef = useRef<HTMLInputElement>(null);
  const renamingId = editing?.kind === "rename" ? editing.conversationId : null;

  useEffect(() => {
    // Focus moves into the name field the user asked to edit.
    if (renamingId !== null) renameRef.current?.select();
  }, [renamingId]);

  useEffect(() => {
    if (menuFor === null) return;
    menuRef.current
      ?.querySelector<HTMLButtonElement>('[role="menuitem"]')
      ?.focus();
  }, [menuFor]);

  const closeMenu = (restoreFocus: boolean) => {
    const trigger =
      menuFor === null ? undefined : triggerRefs.current.get(menuFor);
    setMenuFor(null);
    if (restoreFocus) trigger?.focus();
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitem"]',
      ) ?? [],
    );
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = (index + 1) % items.length;
    else if (event.key === "ArrowUp")
      next = (index - 1 + items.length) % items.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
      return;
    } else if (event.key === "Tab") {
      closeMenu(false);
      return;
    }
    if (next !== null && items.length > 0) {
      event.preventDefault();
      items[next]?.focus();
    }
  };

  const saveRename = async () => {
    if (actions === undefined || editing?.kind !== "rename" || pending) return;
    const title = editing.value.trim();
    if (title.length === 0 || title.length > 120) {
      setEditing({ ...editing, invalid: true });
      return;
    }
    setPending(true);
    const saved = await actions.onRename(editing.conversationId, title);
    setPending(false);
    if (saved) setEditing(null);
  };

  const confirmDelete = async () => {
    if (actions === undefined || editing?.kind !== "delete" || pending) return;
    setPending(true);
    const deleted = await actions.onDelete(editing.conversationId);
    setPending(false);
    if (deleted) setEditing(null);
  };

  const searchBox =
    actions === undefined ? null : (
      <input
        className="tap-chat-history-search"
        type="search"
        aria-label={actions.labels.search}
        placeholder={actions.labels.search}
        value={actions.search}
        maxLength={120}
        onChange={(event) => actions.onSearchChange(event.target.value)}
      />
    );

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
  const searching = actions !== undefined && actions.search.trim().length > 0;
  const rows = searching ? (actions.searchResults ?? []) : conversations;
  if (!searching && conversations.length === 0) {
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
      {searchBox}
      {actions?.error === undefined ? null : (
        <p className="tap-chat-history-state" role="alert">
          {actions.error}
        </p>
      )}
      {searching && actions.isSearching ? (
        <p className="tap-chat-history-state" role="status">
          Loading conversations…
        </p>
      ) : searching && rows.length === 0 ? (
        <p className="tap-chat-history-state" role="status">
          {actions.labels.noMatches}
        </p>
      ) : null}
      {rows.map((conversation, index) => {
        const label = searching
          ? conversation.title
          : getLabel(conversation, index);
        const id = conversation.conversationId;
        if (editing?.conversationId === id && editing.kind === "rename") {
          return (
            <form
              key={id}
              className="tap-chat-history-edit"
              onSubmit={(event) => {
                event.preventDefault();
                void saveRename();
              }}
            >
              <input
                ref={renameRef}
                aria-label={actions!.labels.chatName}
                aria-invalid={editing.invalid || undefined}
                value={editing.value}
                maxLength={120}
                disabled={pending}
                onChange={(event) =>
                  setEditing({
                    ...editing,
                    value: event.target.value,
                    invalid: false,
                  })
                }
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    event.preventDefault();
                    setEditing(null);
                    triggerRefs.current.get(id)?.focus();
                  }
                }}
              />
              {editing.invalid ? (
                <p role="alert">{actions!.labels.chatNameInvalid}</p>
              ) : null}
            </form>
          );
        }
        return (
          <div key={id} className="tap-chat-history-row">
            <button
              type="button"
              aria-label={label}
              aria-current={id === activeId ? "page" : undefined}
              title={label}
              onClick={() => onSelect(id)}
            >
              {icon}
              <span>{label}</span>
            </button>
            {actions === undefined ||
            actions.canManage?.(id) === false ? null : (
              <button
                ref={(element) => {
                  if (element === null) triggerRefs.current.delete(id);
                  else triggerRefs.current.set(id, element);
                }}
                type="button"
                className="tap-chat-history-more"
                aria-label={actions.labels.moreOptions(conversation.title)}
                aria-haspopup="menu"
                aria-expanded={menuFor === id}
                onClick={() => setMenuFor(menuFor === id ? null : id)}
              >
                <span aria-hidden="true">⋯</span>
              </button>
            )}
            {menuFor === id && actions !== undefined ? (
              <div
                ref={menuRef}
                className="tap-chat-history-menu"
                role="menu"
                aria-label={actions.labels.moreOptions(conversation.title)}
                onKeyDown={handleMenuKeyDown}
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuFor(null);
                    setEditing({
                      kind: "rename",
                      conversationId: id,
                      value: conversation.title,
                      invalid: false,
                    });
                  }}
                >
                  {actions.labels.rename}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  data-danger="true"
                  onClick={() => {
                    setMenuFor(null);
                    setEditing({ kind: "delete", conversationId: id });
                  }}
                >
                  {actions.labels.delete}
                </button>
              </div>
            ) : null}
            {editing?.kind === "delete" &&
            editing.conversationId === id &&
            actions !== undefined ? (
              <div
                className="tap-chat-history-confirm"
                role="alertdialog"
                aria-label={actions.labels.confirmDelete}
              >
                <p>
                  <strong>{actions.labels.confirmDelete}</strong>{" "}
                  {actions.labels.deleteWarning}
                </p>
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => {
                    setEditing(null);
                    triggerRefs.current.get(id)?.focus();
                  }}
                >
                  {actions.labels.cancel}
                </button>
                <button
                  type="button"
                  data-danger="true"
                  disabled={pending}
                  onClick={() => void confirmDelete()}
                >
                  {actions.labels.delete}
                </button>
              </div>
            ) : null}
          </div>
        );
      })}
      {hasMore && !searching ? (
        <button type="button" disabled={isLoadingMore} onClick={onLoadMore}>
          {isLoadingMore ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </nav>
  );
}
