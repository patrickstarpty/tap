import {
  BarChartOutlined,
  BookOutlined,
  CodeOutlined,
  FileTextOutlined,
  FormOutlined,
  MessageOutlined,
  RobotOutlined,
  ToolOutlined,
} from "@ant-design/icons";

import type { PrototypeCopy } from "./copy";
import type { Conversation, Locale, ProductModule } from "./model";
import { PanelToggleIcon } from "./PanelToggleIcon";
import { ConversationHistory } from "../../../features/conversations/components/ConversationHistory";

const tapperListeningAvatar = new URL(
  "../../../../assets/brand/tapper/listening/svg/avatar/tapper-listening-avatar-color.svg?no-inline",
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
  onSelectConversation: (conversationId: string) => void;
  onToggleCollapsed: () => void;
  historyState?: {
    error?: string;
    hasMore: boolean;
    isLoading: boolean;
    isLoadingMore: boolean;
    onLoadMore: () => void;
    onRetry: () => void;
  };
}

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
  onSelectConversation,
  onToggleCollapsed,
  historyState,
}: PrototypeSidebarProps) {
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
          src={tapperListeningAvatar}
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
      key: "test-analytics",
      label: copy.navigation["test-analytics"],
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

  const conversationHistory = conversations
    .map((conversation, index) => ({ conversation, index }))
    .filter(({ conversation }) => {
      if (historyState !== undefined && conversation.id !== "draft") {
        return true;
      }
      const contextCount =
        conversation.selectedSourceIds.length +
        conversation.selectedAgentIds.length +
        conversation.selectedSkillIds.length;
      return conversation.turns.length > 0 || contextCount > 0;
    });

  const getConversationLabel = (conversation: Conversation) => {
    const contextCount =
      conversation.selectedSourceIds.length +
      conversation.selectedAgentIds.length +
      conversation.selectedSkillIds.length;
    const title =
      conversation.turns.length > 0 ||
      (historyState !== undefined && conversation.id !== "draft")
        ? conversation.title
        : copy.navigation.newChat;
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

        <div className="tap-sidebar-footer">
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
        </div>
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

        {conversationHistory.length > 0 || historyState !== undefined ? (
          <ConversationHistory
            activeId={activeConversationId}
            ariaLabel={copy.navigation.chatHistory}
            conversations={conversationHistory.map(({ conversation }) => ({
              conversationId: conversation.id,
              title: getConversationLabel(conversation),
              createdAt: "1970-01-01T00:00:00Z",
              updatedAt: "1970-01-01T00:00:00Z",
            }))}
            error={historyState?.error}
            hasMore={historyState?.hasMore}
            icon={<MessageOutlined aria-hidden="true" />}
            isLoading={historyState?.isLoading}
            isLoadingMore={historyState?.isLoadingMore}
            onLoadMore={historyState?.onLoadMore ?? (() => undefined)}
            onRetry={historyState?.onRetry ?? (() => undefined)}
            onSelect={onSelectConversation}
            sectionTitle={copy.navigation.chatHistory}
          />
        ) : null}
      </aside>
    </>
  );
}
