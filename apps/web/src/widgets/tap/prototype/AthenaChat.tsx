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

  useEffect(() => {
    setMessage("");
    setMenuOpen(false);
    setPicker(null);
    setPickerQuery("");
  }, [conversation.id]);

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
      };
    }
    if (picker === "agents") {
      return {
        title: copy.composer.useAgents,
        search: copy.composer.searchAgents,
        items: agents.map((agent) => ({ id: agent.id, name: agent.name })),
        onSelect: onToggleAgent,
      };
    }
    if (picker === "skills") {
      return {
        title: copy.composer.useSkills,
        search: copy.composer.searchSkills,
        items: skills.map((skill) => ({ id: skill.id, name: skill.name })),
        onSelect: onToggleSkill,
      };
    }
    return null;
  }, [
    agents,
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
        <div className="tap-context-chips" aria-label="Message context">
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
          <Button
            type="text"
            shape="circle"
            htmlType="button"
            aria-label={copy.composer.addToMessage}
            aria-expanded={menuOpen}
            icon={<PlusOutlined aria-hidden="true" />}
            onClick={() => setMenuOpen((current) => !current)}
          />
          {menuOpen ? (
            <div
              className="tap-composer-menu"
              role="menu"
              aria-label={copy.composer.addToMessage}
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
            className="tap-context-picker"
            role="dialog"
            aria-modal="true"
            aria-label={pickerConfig.title}
          >
            <header>
              <h2>{pickerConfig.title}</h2>
              <Button
                type="text"
                shape="circle"
                aria-label={`Close ${pickerConfig.title}`}
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
                    aria-selected={false}
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
