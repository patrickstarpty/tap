import {
  BookOutlined,
  CodeOutlined,
  FileTextOutlined,
  MessageOutlined,
  PlusOutlined,
  RobotOutlined,
  ToolOutlined,
} from "@ant-design/icons";

import type { PrototypeCopy } from "./copy";
import type { Conversation, Locale, ProductModule } from "./model";

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
}: PrototypeSidebarProps) {
  const modules: readonly {
    icon: React.ReactNode;
    key: ProductModule;
    label: string;
  }[] = [
    {
      key: "athena",
      label: copy.navigation.athena,
      icon: <MessageOutlined aria-hidden="true" />,
    },
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
  ];

  const conversationHistory = conversations
    .map((conversation, index) => ({ conversation, index }))
    .filter(({ conversation }) => {
      const contextCount =
        conversation.selectedSourceIds.length +
        conversation.selectedAgentIds.length +
        conversation.selectedSkillIds.length;

      return (
        conversation.id !== activeConversationId &&
        (conversation.turns.length > 0 || contextCount > 0)
      );
    });

  const getConversationLabel = (conversation: Conversation, index: number) => {
    const contextCount =
      conversation.selectedSourceIds.length +
      conversation.selectedAgentIds.length +
      conversation.selectedSkillIds.length;
    const title =
      conversation.turns.length > 0
        ? conversation.title
        : copy.navigation.newChat;
    const contextLabel =
      contextCount > 0 ? ` · ${contextCount} ${copy.sources.selected}` : "";

    return `${title} · ${copy.chat.conversation} ${index + 1}${contextLabel}`;
  };

  const moduleButton = (module: (typeof modules)[number]) => (
    <button
      key={module.key}
      type="button"
      className="tap-navigation-item"
      aria-label={module.label}
      aria-current={activeModule === module.key ? "page" : undefined}
      title={collapsed ? module.label : undefined}
      onClick={() => onModuleChange(module.key)}
    >
      {module.icon}
      <span className="tap-sidebar-label">{module.label}</span>
    </button>
  );

  return (
    <aside
      id="tap-product-sidebar"
      className="tap-sidebar"
      data-collapsed={collapsed}
    >
      <div className="tap-sidebar-topline">
        <div className="tap-brand" aria-label="TAP">
          <span>T</span>
          <strong>TAP</strong>
        </div>
        <button
          type="button"
          className="tap-sidebar-collapse"
          aria-controls="tap-product-sidebar"
          aria-expanded={!collapsed}
          aria-label={
            collapsed
              ? copy.navigation.expandSidebar
              : copy.navigation.collapseSidebar
          }
          onClick={onToggleCollapsed}
        >
          <span aria-hidden="true">{collapsed ? "›" : "‹"}</span>
        </button>
      </div>

      <nav
        aria-label={copy.navigation.product}
        className="tap-primary-navigation"
        data-collapsed={String(collapsed)}
      >
        {moduleButton(modules[0])}
        <button
          type="button"
          className="tap-navigation-item tap-navigation-item--new"
          aria-label={copy.navigation.newChat}
          title={collapsed ? copy.navigation.newChat : undefined}
          onClick={onNewChat}
        >
          <PlusOutlined aria-hidden="true" />
          <span className="tap-sidebar-label">{copy.navigation.newChat}</span>
        </button>
        {modules.slice(1).map(moduleButton)}
      </nav>

      {conversationHistory.length > 0 ? (
        <nav
          className="tap-chat-history"
          aria-label={copy.navigation.chatHistory}
        >
          <span className="tap-sidebar-section-title">
            {copy.navigation.chatHistory}
          </span>
          {conversationHistory.map(({ conversation, index }) => {
            const label = getConversationLabel(conversation, index);

            return (
              <button
                key={conversation.id}
                type="button"
                aria-label={label}
                title={label}
                onClick={() => onSelectConversation(conversation.id)}
              >
                <MessageOutlined aria-hidden="true" />
                <span>{label}</span>
              </button>
            );
          })}
        </nav>
      ) : null}

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
              {copy.language[language]}
            </button>
          ))}
        </div>
        <span className="tap-avatar">PT</span>
        <span className="tap-sidebar-copy">
          <strong>{copy.navigation.prototypeTeam}</strong>
          <small>{copy.navigation.localWorkspace}</small>
        </span>
      </div>
    </aside>
  );
}
