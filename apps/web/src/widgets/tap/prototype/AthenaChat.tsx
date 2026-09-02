import {
  BookOutlined,
  CloseOutlined,
  CodeOutlined,
  MessageOutlined,
  PlusOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { Button, Input } from "antd";
import type { TextAreaRef } from "antd/es/input/TextArea";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";

import type { PrototypeCopy } from "./copy";
import type {
  AssistantTurn,
  CatalogItem,
  Conversation,
  LibrarySource,
} from "./model";

type PickerKind = "library" | "agents" | "skills";

interface AthenaChatProps {
  agents: readonly CatalogItem[];
  conversation: Conversation;
  copy: PrototypeCopy;
  onSend: (prompt: string) => void;
  onToggleAgent: (agentId: string) => void;
  onToggleSkill: (skillId: string) => void;
  onToggleSource: (sourceId: string) => void;
  renderAssistantTurn: (turn: AssistantTurn) => ReactNode;
  skills: readonly CatalogItem[];
  sources: readonly LibrarySource[];
}

export function AthenaChat({
  agents,
  conversation,
  copy,
  onSend,
  onToggleAgent,
  onToggleSkill,
  onToggleSource,
  renderAssistantTurn,
  skills,
  sources,
}: AthenaChatProps) {
  const [message, setMessage] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [picker, setPicker] = useState<PickerKind | null>(null);
  const [pickerQuery, setPickerQuery] = useState("");
  const composerRef = useRef<TextAreaRef>(null);
  const addTriggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const wasMenuOpenRef = useRef(false);
  const previousPickerRef = useRef<PickerKind | null>(null);

  useEffect(() => {
    setMessage("");
    setMenuOpen(false);
    setPicker(null);
    setPickerQuery("");
  }, [conversation.id]);

  useEffect(() => {
    if (menuOpen) {
      menuRef.current
        ?.querySelector<HTMLButtonElement>('[role="menuitem"]')
        ?.focus();
    } else if (wasMenuOpenRef.current && picker === null) {
      addTriggerRef.current?.focus();
    }
    wasMenuOpenRef.current = menuOpen;
  }, [menuOpen, picker]);

  useEffect(() => {
    if (picker === null && previousPickerRef.current !== null) {
      addTriggerRef.current?.focus();
    }
    previousPickerRef.current = picker;
  }, [picker]);

  const selectedSources = sources.filter((source) =>
    conversation.selectedSourceIds.includes(source.id),
  );
  const selectedAgents = agents.filter((agent) =>
    conversation.selectedAgentIds.includes(agent.id),
  );
  const selectedSkills = skills.filter((skill) =>
    conversation.selectedSkillIds.includes(skill.id),
  );

  const pickerConfig = useMemo(() => {
    if (picker === "library") {
      return {
        title: copy.composer.addFromLibrary,
        search: copy.composer.searchLibrary,
        items: sources
          .filter((source) => source.status === "ready")
          .map((source) => ({ id: source.id, name: source.name })),
        onSelect: onToggleSource,
        selectedIds: conversation.selectedSourceIds,
      };
    }
    if (picker === "agents") {
      return {
        title: copy.composer.useAgents,
        search: copy.composer.searchAgents,
        items: agents.map((agent) => ({ id: agent.id, name: agent.name })),
        onSelect: onToggleAgent,
        selectedIds: conversation.selectedAgentIds,
      };
    }
    if (picker === "skills") {
      return {
        title: copy.composer.useSkills,
        search: copy.composer.searchSkills,
        items: skills.map((skill) => ({ id: skill.id, name: skill.name })),
        onSelect: onToggleSkill,
        selectedIds: conversation.selectedSkillIds,
      };
    }
    return null;
  }, [
    agents,
    conversation.selectedAgentIds,
    conversation.selectedSkillIds,
    conversation.selectedSourceIds,
    copy,
    onToggleAgent,
    onToggleSkill,
    onToggleSource,
    picker,
    skills,
    sources,
  ]);

  const visiblePickerItems = useMemo(() => {
    if (pickerConfig === null) return [];
    const normalized = pickerQuery.trim().toLowerCase();
    if (normalized.length === 0) return pickerConfig.items;
    return pickerConfig.items.filter((item) =>
      item.name.toLowerCase().includes(normalized),
    );
  }, [pickerConfig, pickerQuery]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const prompt = message.trim();
    if (prompt.length === 0) return;
    onSend(prompt);
    setMessage("");
    composerRef.current?.focus();
  };

  const handleComposerKeyDown = (
    event: ReactKeyboardEvent<HTMLTextAreaElement>,
  ) => {
    if (
      event.key !== "Enter" ||
      event.shiftKey ||
      event.nativeEvent.isComposing ||
      event.nativeEvent.keyCode === 229
    ) {
      return;
    }
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  };

  const fillPrompt = (prompt: string) => {
    setMessage(prompt);
    composerRef.current?.focus();
  };

  const openPicker = (kind: PickerKind) => {
    setMenuOpen(false);
    setPickerQuery("");
    setPicker(kind);
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

    if (event.key === "ArrowDown") {
      nextIndex = (activeIndex + 1) % items.length;
    } else if (event.key === "ArrowUp") {
      nextIndex = (activeIndex - 1 + items.length) % items.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = items.length - 1;
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setMenuOpen(false);
      return;
    }

    if (nextIndex !== null && items.length > 0) {
      event.preventDefault();
      items[nextIndex]?.focus();
    }
  };

  const handleDialogKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setPicker(null);
      return;
    }
    if (event.key !== "Tab") return;

    const focusableElements = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    );
    const firstElement = focusableElements[0];
    const lastElement = focusableElements.at(-1);

    if (event.shiftKey && document.activeElement === firstElement) {
      event.preventDefault();
      lastElement?.focus();
    } else if (!event.shiftKey && document.activeElement === lastElement) {
      event.preventDefault();
      firstElement?.focus();
    }
  };

  const composer = (
    <form
      className="tap-composer"
      aria-label={copy.chat.messageComposer}
      onSubmit={submit}
    >
      <label className="athena-visually-hidden" htmlFor="tap-message">
        {copy.chat.messageAthena}
      </label>
      <Input.TextArea
        ref={composerRef}
        id="tap-message"
        value={message}
        rows={3}
        placeholder={copy.chat.placeholder}
        onChange={(event) => setMessage(event.target.value)}
        onKeyDown={handleComposerKeyDown}
      />

      {selectedSources.length + selectedAgents.length + selectedSkills.length >
      0 ? (
        <div
          className="tap-context-chips"
          aria-label={copy.composer.messageContext}
        >
          {selectedSources.map((source) => (
            <span key={`source-${source.id}`}>{source.name}</span>
          ))}
          {selectedAgents.map((agent) => (
            <span key={`agent-${agent.id}`}>{agent.name}</span>
          ))}
          {selectedSkills.map((skill) => (
            <span key={`skill-${skill.id}`}>{skill.name}</span>
          ))}
        </div>
      ) : null}

      <div className="tap-composer-footer">
        <div className="tap-composer-context-control">
          <button
            ref={addTriggerRef}
            type="button"
            className="tap-composer-add-button"
            aria-label={copy.composer.addToMessage}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((current) => !current)}
          >
            <PlusOutlined aria-hidden="true" />
          </button>
          {menuOpen ? (
            <div
              ref={menuRef}
              className="tap-composer-menu"
              role="menu"
              aria-label={copy.composer.addToMessage}
              onKeyDown={handleMenuKeyDown}
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => openPicker("library")}
              >
                <BookOutlined aria-hidden="true" />
                {copy.composer.addFromLibrary}
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => openPicker("agents")}
              >
                <MessageOutlined aria-hidden="true" />
                {copy.composer.useAgents}
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => openPicker("skills")}
              >
                <CodeOutlined aria-hidden="true" />
                {copy.composer.useSkills}
              </button>
            </div>
          ) : null}
        </div>
        <span>{copy.chat.sourceHint}</span>
        <Button
          type="primary"
          shape="circle"
          htmlType="submit"
          aria-label={copy.chat.send}
          disabled={message.trim().length === 0}
          icon={<SendOutlined aria-hidden="true" />}
        />
      </div>
    </form>
  );

  const hasTurns = conversation.turns.length > 0;

  return (
    <section
      className={`tap-chat ${hasTurns ? "tap-chat--active" : "tap-chat--idle"}`}
      aria-label={hasTurns ? "Athena assistant" : copy.chat.startConversation}
    >
      {hasTurns ? (
        <div
          className="tap-chat-transcript"
          role="log"
          aria-label={copy.chat.conversation}
          aria-live="polite"
        >
          <div className="tap-conversation">
            {conversation.turns.map((turn) => (
              <div className="tap-turn" key={turn.id}>
                <div className="tap-user-message">{turn.prompt}</div>
                <div className="tap-assistant-message">
                  <span className="tap-assistant-avatar">A</span>
                  {renderAssistantTurn(turn)}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="tap-chat-welcome">
          <div className="tap-athena-mark" aria-hidden="true">
            A
          </div>
          <h1>{copy.chat.heading}</h1>
          <p>{copy.chat.description}</p>
        </div>
      )}

      {composer}

      {!hasTurns ? (
        <div
          className="tap-quick-prompts"
          aria-label={copy.chat.suggestedPrompts}
        >
          {copy.chat.quickPrompts.map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => fillPrompt(prompt)}
            >
              {prompt}
            </button>
          ))}
        </div>
      ) : null}

      {pickerConfig === null ? null : (
        <div className="tap-picker-backdrop">
          <section
            ref={dialogRef}
            className="tap-context-picker"
            role="dialog"
            aria-modal="true"
            aria-label={pickerConfig.title}
            onKeyDown={handleDialogKeyDown}
          >
            <header>
              <h2>{pickerConfig.title}</h2>
              <Button
                type="text"
                shape="circle"
                aria-label={`${copy.composer.close} ${pickerConfig.title}`}
                icon={<CloseOutlined aria-hidden="true" />}
                onClick={() => setPicker(null)}
              />
            </header>
            <Input
              autoFocus
              aria-label={pickerConfig.search}
              placeholder={pickerConfig.search}
              value={pickerQuery}
              onChange={(event) => setPickerQuery(event.target.value)}
            />
            <div className="tap-picker-options" role="listbox">
              {visiblePickerItems.length > 0 ? (
                visiblePickerItems.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="option"
                    aria-label={item.name}
                    aria-selected={pickerConfig.selectedIds.includes(item.id)}
                    onClick={() => {
                      pickerConfig.onSelect(item.id);
                      setPicker(null);
                    }}
                  >
                    {item.name}
                  </button>
                ))
              ) : (
                <p>{copy.catalog.noResults}</p>
              )}
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
