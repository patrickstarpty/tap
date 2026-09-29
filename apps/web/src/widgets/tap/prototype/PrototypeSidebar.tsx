import {
  BarChartOutlined,
  BookOutlined,
  CodeOutlined,
  DeleteOutlined,
  EditOutlined,
  FileTextOutlined,
  FormOutlined,
  MessageOutlined,
  MoreOutlined,
  RobotOutlined,
  SearchOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { Button } from "antd";
import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

import { AccessibleDialog } from "../../../legacy/AccessibleDialog";

import type { PrototypeCopy } from "./copy";
import type { Conversation, Locale, ProductModule } from "./model";
import { PanelToggleIcon } from "./PanelToggleIcon";
import { clearPrototypeFault, isPrototypeFaultActive } from "./prototypeFaults";

const tapperAvatar = new URL(
  "../../../../assets/brand/tapper/owl/svg/avatar/tapper-owl-avatar-color.svg?no-inline",
  import.meta.url,
).href;
const tapperWordmark = new URL(
  "../../../../assets/brand/tapper/svg/tapper-wordmark-ink.svg?no-inline",
  import.meta.url,
).href;

interface PrototypeSidebarProps {
  activeConversationId: string;
  activeModule: ProductModule;
  collapsed: boolean;
  conversations: readonly Conversation[];
  copy: PrototypeCopy;
  locale: Locale;
  onLocaleChange: (locale: Locale) => void;
  onModuleChange: (module: ProductModule) => void;
  onNewChat: () => void;
  /** History actions appear only where the shell can rename and delete chats. */
  onDeleteConversation?: (conversationId: string) => boolean;
  onRenameConversation?: (conversationId: string, title: string) => void;
  onSelectConversation: (conversationId: string) => void;
  onToggleCollapsed: () => void;
  showFooter?: boolean;
}

export const CONVERSATION_TITLE_MAX_LENGTH = 120;
export const HISTORY_PAGE_SIZE = 10;

export function PrototypeSidebar({
  activeConversationId,
  activeModule,
  collapsed,
  conversations,
  copy,
  locale,
  onLocaleChange,
  onModuleChange,
  onNewChat,
  onDeleteConversation,
  onRenameConversation,
  onSelectConversation,
  onToggleCollapsed,
  showFooter = true,
}: PrototypeSidebarProps) {
  const [historyQuery, setHistoryQuery] = useState("");
  const [menuConversationId, setMenuConversationId] = useState<string | null>(
    null,
  );
  const [renaming, setRenaming] = useState<{
    conversationId: string;
    value: string;
    invalid: boolean;
  } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const [deleteFailed, setDeleteFailed] = useState(false);
  const [historyLoadFailed, setHistoryLoadFailed] = useState(() =>
    isPrototypeFaultActive("history-load-failed"),
  );
  const [visibleHistoryCount, setVisibleHistoryCount] =
    useState(HISTORY_PAGE_SIZE);
  const renamingRef = useRef(renaming);
  renamingRef.current = renaming;
  const menuRef = useRef<HTMLDivElement>(null);
  const menuTriggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const searchRef = useRef<HTMLInputElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (menuConversationId === null) return;
    menuRef.current
      ?.querySelector<HTMLButtonElement>('[role="menuitem"]')
      ?.focus();
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (
        !menuRef.current?.contains(target) &&
        !menuTriggerRefs.current.get(menuConversationId)?.contains(target)
      )
        setMenuConversationId(null);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer, true);
    return () =>
      document.removeEventListener("pointerdown", closeOnOutsidePointer, true);
  }, [menuConversationId]);

  useEffect(() => {
    if (renaming === null) return;
    renameInputRef.current?.focus();
    renameInputRef.current?.select();
    // Focus and select only when an inline rename starts.
  }, [renaming?.conversationId]);
  const tapperWorkspaceActive = [
    "tapper",
    "agents",
    "skills",
    "library",
  ].includes(activeModule);
  const tapperSidebarVisible = tapperWorkspaceActive && !collapsed;
  const productModules: readonly {
    icon: React.ReactNode;
    key: ProductModule;
    label: string;
  }[] = [
    {
      key: "tapper",
      label: copy.navigation.tapper,
      icon: (
        <img
          className="tap-tapper-rail-mark"
          src={tapperAvatar}
          alt=""
        />
      ),
    },
    {
      key: "test-management",
      label: copy.navigation["test-management"],
      icon: <FileTextOutlined aria-hidden="true" />,
    },
    {
      key: "low-code",
      label: copy.navigation["low-code"],
      icon: <CodeOutlined aria-hidden="true" />,
    },
    {
      key: "test-insights",
      label: copy.navigation["test-insights"],
      icon: <BarChartOutlined aria-hidden="true" />,
    },
  ];
  const tapperModules: typeof productModules = [
    {
      key: "agents",
      label: copy.navigation.agents,
      icon: <RobotOutlined aria-hidden="true" />,
    },
    {
      key: "skills",
      label: copy.navigation.skills,
      icon: <ToolOutlined aria-hidden="true" />,
    },
    {
      key: "library",
      label: copy.navigation.library,
      icon: <BookOutlined aria-hidden="true" />,
    },
  ];

  const conversationHistory = conversations.filter((conversation) => {
    const contextCount =
      conversation.selectedSourceIds.length +
      conversation.selectedAgentIds.length +
      conversation.selectedSkillIds.length;

    return conversation.turns.length > 0 || contextCount > 0;
  });

  const getConversationTitle = (conversation: Conversation) =>
    conversation.turns.length > 0 || conversation.title !== "New chat"
      ? conversation.title
      : copy.navigation.newChat;

  const normalizedHistoryQuery = historyQuery.trim().toLowerCase();
  const visibleConversationHistory =
    normalizedHistoryQuery.length === 0
      ? conversationHistory
      : conversationHistory.filter((conversation) =>
          getConversationTitle(conversation)
            .toLowerCase()
            .includes(normalizedHistoryQuery),
        );
  const pagedConversationHistory =
    normalizedHistoryQuery.length === 0
      ? visibleConversationHistory.slice(0, visibleHistoryCount)
      : visibleConversationHistory;
  const canLoadMoreHistory =
    normalizedHistoryQuery.length === 0 &&
    conversationHistory.length > visibleHistoryCount;

  const closeMenu = (restoreFocus: boolean) => {
    const conversationId = menuConversationId;
    setMenuConversationId(null);
    if (restoreFocus && conversationId !== null)
      menuTriggerRefs.current.get(conversationId)?.focus();
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitem"]',
      ) ?? [],
    );
    const activeIndex = items.indexOf(
      document.activeElement as HTMLButtonElement,
    );
    let nextIndex: number | null = null;
    if (event.key === "ArrowDown") nextIndex = (activeIndex + 1) % items.length;
    else if (event.key === "ArrowUp")
      nextIndex = (activeIndex - 1 + items.length) % items.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = items.length - 1;
    else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
      return;
    } else if (event.key === "Tab") {
      closeMenu(false);
      return;
    }
    if (nextIndex !== null && items.length > 0) {
      event.preventDefault();
      items[nextIndex]?.focus();
    }
  };

  const startRename = (conversation: Conversation) => {
    setMenuConversationId(null);
    setRenaming({
      conversationId: conversation.id,
      value: getConversationTitle(conversation),
      invalid: false,
    });
  };

  const finishRename = (save: boolean) => {
    const current = renamingRef.current;
    if (current === null) return;
    const title = current.value.trim();
    if (save) {
      if (title.length === 0 || title.length > CONVERSATION_TITLE_MAX_LENGTH) {
        setRenaming({ ...current, invalid: true });
        return;
      }
      onRenameConversation?.(current.conversationId, title);
    }
    renamingRef.current = null;
    setRenaming(null);
    menuTriggerRefs.current.get(current.conversationId)?.focus();
  };

  const confirmDelete = () => {
    if (deleteTarget === null) return;
    const deletingActive = deleteTarget.id === activeConversationId;
    const deleted = onDeleteConversation?.(deleteTarget.id) ?? true;
    setDeleteTarget(null);
    setDeleteFailed(!deleted);
    if (deleted && !deletingActive) searchRef.current?.focus();
  };

  const getConversationLabel = (conversation: Conversation) => {
    const contextCount =
      conversation.selectedSourceIds.length +
      conversation.selectedAgentIds.length +
      conversation.selectedSkillIds.length;
    const title = getConversationTitle(conversation);
    const contextLabel =
      contextCount > 0 ? ` · ${contextCount} ${copy.sources.selected}` : "";

    return `${title}${contextLabel}`;
  };

  const moduleButton = (
    module: (typeof productModules)[number],
    location: "product" | "tapper",
  ) => {
    const isActive =
      module.key === "tapper"
        ? tapperWorkspaceActive
        : activeModule === module.key;

    return (
      <button
        key={module.key}
        type="button"
        className={`tap-navigation-item tap-navigation-item--${location}`}
        aria-label={module.label}
        aria-current={isActive ? "page" : undefined}
        aria-controls={
          module.key === "tapper" ? "tap-tapper-sidebar" : undefined
        }
        aria-expanded={
          module.key === "tapper" ? tapperSidebarVisible : undefined
        }
        title={location === "product" || collapsed ? module.label : undefined}
        onClick={() => onModuleChange(module.key)}
      >
        {module.icon}
        <span className="tap-sidebar-label">{module.label}</span>
      </button>
    );
  };

  return (
    <>
      <aside
        id="tap-product-sidebar"
        className="tap-product-rail"
        aria-label={copy.navigation.product}
      >
        <div className="tap-brand" role="img" aria-label="TAP platform">
          <span aria-hidden="true">TAP</span>
        </div>

        <nav
          aria-label={copy.navigation.product}
          className="tap-primary-navigation"
        >
          {productModules.map((module) => moduleButton(module, "product"))}
        </nav>

        {showFooter ? <div className="tap-sidebar-footer">
          <div
            className="tap-language-switcher"
            aria-label={copy.navigation.language}
          >
            {(["en", "zh"] as const).map((language) => (
              <button
                key={language}
                type="button"
                aria-label={copy.language[language]}
                aria-pressed={locale === language}
                onClick={() => onLocaleChange(language)}
              >
                {language === "en" ? "EN" : "中"}
              </button>
            ))}
          </div>
          <span
            className="tap-avatar"
            aria-label={copy.navigation.prototypeTeam}
            title={`${copy.navigation.prototypeTeam} · ${copy.navigation.localWorkspace}`}
          >
            PT
          </span>
          <span className="tap-sidebar-copy">
            <strong>{copy.navigation.prototypeTeam}</strong>
            <small>{copy.navigation.localWorkspace}</small>
          </span>
        </div> : null}
      </aside>

      <aside
        id="tap-tapper-sidebar"
        className="tap-tapper-sidebar"
        aria-hidden={tapperWorkspaceActive ? undefined : true}
        aria-label={copy.navigation.tapperTools}
        data-collapsed={collapsed}
        data-inactive={!tapperWorkspaceActive}
        inert={tapperWorkspaceActive ? undefined : true}
      >
        <div className="tap-tapper-sidebar-header">
          <h2 aria-label={copy.navigation.tapper}>
            <img className="tap-tapper-wordmark" src={tapperWordmark} alt="" />
          </h2>
          <button
            type="button"
            className={`tap-panel-toggle tap-panel-toggle--left-${collapsed ? "expand" : "collapse"}`}
            aria-controls="tap-tapper-sidebar"
            aria-expanded={!collapsed}
            aria-label={
              collapsed
                ? copy.navigation.expandSidebar
                : copy.navigation.collapseSidebar
            }
            onClick={onToggleCollapsed}
          >
            <PanelToggleIcon
              side="left"
              state={collapsed ? "collapsed" : "expanded"}
            />
          </button>
        </div>

        <nav
          id="tap-tapper-navigation"
          aria-label={copy.navigation.tapperTools}
          className="tap-tapper-navigation"
        >
          <button
            type="button"
            className="tap-navigation-item tap-navigation-item--tapper"
            aria-label={copy.navigation.newChat}
            title={collapsed ? copy.navigation.newChat : undefined}
            aria-current={activeModule === "tapper" ? "page" : undefined}
            onClick={onNewChat}
          >
            <FormOutlined aria-hidden="true" />
            <span className="tap-sidebar-label">{copy.navigation.newChat}</span>
          </button>
          {tapperModules.map((module) => moduleButton(module, "tapper"))}
        </nav>

        {!collapsed &&
        (historyLoadFailed || conversationHistory.length > 0) ? (
          <nav
            className="tap-chat-history"
            aria-label={copy.navigation.chatHistory}
          >
            <span className="tap-sidebar-section-title">
              {copy.navigation.chatHistory}
            </span>
            {historyLoadFailed ? (
              <div className="tap-chat-history-load-error">
                <p role="alert">{copy.navigation.historyLoadFailed}</p>
                <Button
                  onClick={() => {
                    clearPrototypeFault("history-load-failed");
                    setHistoryLoadFailed(false);
                  }}
                >
                  {copy.navigation.retry}
                </Button>
              </div>
            ) : (
              <>
                <label className="tap-chat-history-search">
                  <SearchOutlined aria-hidden="true" />
                  <input
                    ref={searchRef}
                    type="search"
                    aria-label={copy.navigation.searchChats}
                    placeholder={copy.navigation.searchChats}
                    value={historyQuery}
                    onChange={(event) => setHistoryQuery(event.target.value)}
                  />
                </label>
                {deleteFailed ? (
                  <p className="tap-chat-history-delete-error" role="alert">
                    {copy.navigation.deleteFailed}
                  </p>
                ) : null}
                {pagedConversationHistory.length === 0 ? (
                  <p className="tap-chat-history-empty" role="status">
                    {copy.navigation.noMatchingChats}
                  </p>
                ) : null}
                {pagedConversationHistory.map((conversation) => {
                  const label = getConversationLabel(conversation);
                  const title = getConversationTitle(conversation);
                  const menuOpen = menuConversationId === conversation.id;
                  const isRenaming = renaming?.conversationId === conversation.id;
                  const renameHintId = `tap-chat-rename-hint-${conversation.id}`;

                  return (
                    <div
                      key={conversation.id}
                      className="tap-chat-history-row"
                      data-active={conversation.id === activeConversationId}
                      data-menu-open={menuOpen || undefined}
                    >
                      {isRenaming ? (
                        <div className="tap-chat-history-rename">
                          <MessageOutlined aria-hidden="true" />
                          <input
                            ref={renameInputRef}
                            aria-label={copy.navigation.chatName}
                            aria-invalid={renaming?.invalid || undefined}
                            aria-describedby={
                              renaming?.invalid ? renameHintId : undefined
                            }
                            maxLength={CONVERSATION_TITLE_MAX_LENGTH}
                            value={renaming?.value ?? ""}
                            onChange={(event) =>
                              setRenaming({
                                conversationId: conversation.id,
                                value: event.target.value,
                                invalid: false,
                              })
                            }
                            onKeyDown={(event) => {
                              if (event.nativeEvent.isComposing) return;
                              if (event.key === "Enter") {
                                event.preventDefault();
                                finishRename(true);
                              } else if (event.key === "Escape") {
                                event.preventDefault();
                                event.stopPropagation();
                                finishRename(false);
                              }
                            }}
                            onBlur={() => {
                              const current = renamingRef.current;
                              if (current?.conversationId !== conversation.id)
                                return;
                              const trimmed = current.value.trim();
                              finishRename(
                                trimmed.length > 0 &&
                                  trimmed.length <= CONVERSATION_TITLE_MAX_LENGTH,
                              );
                            }}
                          />
                          {renaming?.invalid ? (
                            <small id={renameHintId} role="alert">
                              {copy.navigation.chatNameHint}
                            </small>
                          ) : null}
                        </div>
                      ) : (
                        <button
                          type="button"
                          className="tap-chat-history-item"
                          aria-label={label}
                          aria-current={
                            conversation.id === activeConversationId
                              ? "page"
                              : undefined
                          }
                          title={label}
                          onClick={() => onSelectConversation(conversation.id)}
                        >
                          <MessageOutlined aria-hidden="true" />
                          <span>{label}</span>
                        </button>
                      )}
                      {onRenameConversation === undefined ||
                      onDeleteConversation === undefined ? null : (
                      <button
                        ref={(element) => {
                          if (element === null)
                            menuTriggerRefs.current.delete(conversation.id);
                          else menuTriggerRefs.current.set(conversation.id, element);
                        }}
                        type="button"
                        className="tap-chat-history-more"
                        aria-label={copy.navigation.moreOptionsFor.replace(
                          "{title}",
                          title,
                        )}
                        aria-haspopup="menu"
                        aria-expanded={menuOpen}
                        hidden={isRenaming}
                        onClick={() =>
                          setMenuConversationId(menuOpen ? null : conversation.id)
                        }
                        onKeyDown={(event) => {
                          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                            event.preventDefault();
                            setMenuConversationId(conversation.id);
                          }
                        }}
                      >
                        <MoreOutlined aria-hidden="true" />
                      </button>
                      )}
                      {menuOpen ? (
                        <div
                          ref={menuRef}
                          className="tap-chat-history-menu"
                          role="menu"
                          aria-label={copy.navigation.moreOptionsFor.replace(
                            "{title}",
                            title,
                          )}
                          onKeyDown={handleMenuKeyDown}
                        >
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => startRename(conversation)}
                          >
                            <EditOutlined aria-hidden="true" />
                            {copy.navigation.renameChat}
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            data-danger="true"
                            onClick={() => {
                              setMenuConversationId(null);
                              setDeleteFailed(false);
                              setDeleteTarget(conversation);
                            }}
                          >
                            <DeleteOutlined aria-hidden="true" />
                            {copy.navigation.deleteChat}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
                {canLoadMoreHistory ? (
                  <button
                    type="button"
                    className="tap-chat-history-load-more"
                    onClick={() =>
                      setVisibleHistoryCount(
                        (current) => current + HISTORY_PAGE_SIZE,
                      )
                    }
                  >
                    {copy.navigation.loadMore}
                  </button>
                ) : null}
              </>
            )}
          </nav>
        ) : null}
      </aside>
      {deleteTarget === null ? null : (
        <AccessibleDialog
          ariaLabel={copy.navigation.deleteChatTitle}
          className="tap-catalog-dialog tap-delete-chat-dialog"
          onClose={() => setDeleteTarget(null)}
          opener={menuTriggerRefs.current.get(deleteTarget.id) ?? null}
        >
          <header>
            <h2>{copy.navigation.deleteChatTitle}</h2>
          </header>
          <p>
            {copy.navigation.deleteChatDescription.replace(
              "{title}",
              getConversationTitle(deleteTarget),
            )}
          </p>
          <div className="tap-dialog-actions">
            <Button onClick={() => setDeleteTarget(null)}>
              {copy.navigation.cancel}
            </Button>
            <Button type="primary" danger onClick={confirmDelete}>
              {copy.navigation.deleteChat}
            </Button>
          </div>
        </AccessibleDialog>
      )}
    </>
  );
}
