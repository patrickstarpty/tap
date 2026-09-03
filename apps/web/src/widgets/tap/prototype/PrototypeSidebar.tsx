import {
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
  const athenaWorkspaceActive = [
    "athena",
    "agents",
    "skills",
    "library",
  ].includes(activeModule);
  const athenaSidebarVisible = athenaWorkspaceActive && !collapsed;
  const productModules: readonly {
    icon: React.ReactNode;
    key: ProductModule;
    label: string;
  }[] = [
    {
      key: "athena",
      label: copy.navigation.athena,
      icon: (
        <span className="tap-athena-rail-mark" aria-hidden="true">
          A
        </span>
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
  ];
  const athenaModules: typeof productModules = [
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
      const contextCount =
        conversation.selectedSourceIds.length +
        conversation.selectedAgentIds.length +
        conversation.selectedSkillIds.length;

      return conversation.turns.length > 0 || contextCount > 0;
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

  const moduleButton = (
    module: (typeof productModules)[number],
    location: "product" | "athena",
  ) => {
    const isActive =
      module.key === "athena"
        ? athenaWorkspaceActive
        : activeModule === module.key;

    return (
      <button
        key={module.key}
        type="button"
        className={`tap-navigation-item tap-navigation-item--${location}`}
        aria-label={module.label}
        aria-current={isActive ? "page" : undefined}
        aria-controls={
          module.key === "athena" ? "tap-athena-sidebar" : undefined
        }
        aria-expanded={
          module.key === "athena" ? athenaSidebarVisible : undefined
        }
        title={location === "product" ? module.label : undefined}
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
        <div className="tap-brand" aria-label="TAP">
          <span>T</span>
          <strong>TAP</strong>
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
        id="tap-athena-sidebar"
        className="tap-athena-sidebar"
        aria-hidden={athenaSidebarVisible ? undefined : true}
        aria-label={copy.navigation.athenaTools}
        data-collapsed={!athenaSidebarVisible}
        inert={athenaSidebarVisible ? undefined : true}
      >
        <div className="tap-athena-sidebar-header">
          <h2>{copy.navigation.athena}</h2>
          <button
            type="button"
            className="tap-panel-toggle tap-panel-toggle--left-collapse"
            aria-controls="tap-athena-sidebar"
            aria-expanded="true"
            aria-label={copy.navigation.collapseSidebar}
            onClick={onToggleCollapsed}
          >
            <PanelToggleIcon side="left" state="expanded" />
          </button>
        </div>

        <nav
          id="tap-athena-navigation"
          aria-label={copy.navigation.athenaTools}
          className="tap-athena-navigation"
        >
          <button
            type="button"
            className="tap-navigation-item tap-navigation-item--athena"
            aria-label={copy.navigation.newChat}
            onClick={onNewChat}
          >
            <FormOutlined aria-hidden="true" />
            <span className="tap-sidebar-label">{copy.navigation.newChat}</span>
          </button>
          {athenaModules.map((module) => moduleButton(module, "athena"))}
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
              );
            })}
          </nav>
        ) : null}
      </aside>
    </>
  );
}
