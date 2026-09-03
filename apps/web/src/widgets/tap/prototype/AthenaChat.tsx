import {
  BookOutlined,
  CheckOutlined,
  CloseOutlined,
  DownOutlined,
  PlusOutlined,
  RobotOutlined,
  SendOutlined,
  ToolOutlined,
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
  type WheelEvent as ReactWheelEvent,
} from "react";

import type { PrototypeCopy } from "./copy";
import type {
  AssistantTurn,
  CatalogItem,
  CodexModelId,
  Conversation,
  LibrarySource,
} from "./model";
import { CODEX_MODELS } from "./model";
import { AccessibleDialog } from "./AccessibleDialog";

type PickerKind = "library" | "agents" | "skills";

interface AthenaChatProps {
  agents: readonly CatalogItem[];
  conversation: Conversation;
  copy: PrototypeCopy;
  isInert?: boolean;
  onModelChange: (modelId: CodexModelId) => void;
  onSend: (prompt: string) => void;
  onToggleAgent: (agentId: string) => void;
  onToggleSkill: (skillId: string) => void;
  onToggleSource: (sourceId: string) => void;
  renderAssistantTurn: (turn: AssistantTurn) => ReactNode;
  skills: readonly CatalogItem[];
  sources: readonly LibrarySource[];
}

const QUESTION_RAIL_ROW_HEIGHT = 14;
const QUESTION_RAIL_VERTICAL_INSET = 64;
const QUESTION_RAIL_MINIMUM_CAPACITY = 3;
const QUESTION_RAIL_WHEEL_STEP = 3;

function getQuestionRailCapacity(availableHeight: number): number {
  const usableHeight = Math.max(
    QUESTION_RAIL_ROW_HEIGHT * QUESTION_RAIL_MINIMUM_CAPACITY,
    availableHeight - QUESTION_RAIL_VERTICAL_INSET,
  );
  const rawCapacity = Math.max(
    QUESTION_RAIL_MINIMUM_CAPACITY,
    Math.floor(usableHeight / QUESTION_RAIL_ROW_HEIGHT),
  );
  return rawCapacity % 2 === 0 ? rawCapacity - 1 : rawCapacity;
}

function getQuestionRailMaximumHeight(availableHeight: number): number {
  return Math.min(
    availableHeight,
    Math.max(
      QUESTION_RAIL_ROW_HEIGHT * QUESTION_RAIL_MINIMUM_CAPACITY,
      availableHeight - QUESTION_RAIL_VERTICAL_INSET,
    ),
  );
}

function clampQuestionWindowStart(
  start: number,
  totalQuestions: number,
  visibleQuestions: number,
): number {
  return Math.max(0, Math.min(start, totalQuestions - visibleQuestions));
}

function formatQuestionCount(
  singularTemplate: string,
  pluralTemplate: string,
  count: number,
): string {
  return (count === 1 ? singularTemplate : pluralTemplate).replace(
    "{count}",
    String(count),
  );
}

export function AthenaChat({
  agents,
  conversation,
  copy,
  isInert = false,
  onModelChange,
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
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [picker, setPicker] = useState<PickerKind | null>(null);
  const [pickerQuery, setPickerQuery] = useState("");
  const composerRef = useRef<TextAreaRef>(null);
  const composerFormRef = useRef<HTMLFormElement>(null);
  const addTriggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const modelMenuRef = useRef<HTMLDivElement>(null);
  const modelTriggerRef = useRef<HTMLButtonElement>(null);
  const wasMenuOpenRef = useRef(false);
  const wasModelMenuOpenRef = useRef(false);
  const chatRef = useRef<HTMLElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const turnElementsRef = useRef(new Map<string, HTMLDivElement>());
  const [activeQuestionId, setActiveQuestionId] = useState<string | null>(null);
  const [questionRailCapacity, setQuestionRailCapacity] = useState(() =>
    getQuestionRailCapacity(
      typeof window === "undefined" ? 900 : window.innerHeight,
    ),
  );
  const [questionRailBoundaryHeight, setQuestionRailBoundaryHeight] = useState(
    () => (typeof window === "undefined" ? 900 : window.innerHeight),
  );
  const [questionWindowStart, setQuestionWindowStart] = useState(0);
  const [questionPreview, setQuestionPreview] = useState<{
    content: string;
    left: number;
    previewId: string;
    top: number;
    turnId: string | null;
  } | null>(null);
  const hasTurns = conversation.turns.length > 0;

  useEffect(() => {
    setMessage("");
    setMenuOpen(false);
    setModelMenuOpen(false);
    setPicker(null);
    setPickerQuery("");
  }, [conversation.id]);

  useEffect(() => {
    setActiveQuestionId(
      conversation.turns[conversation.turns.length - 1]?.id ?? null,
    );
    setQuestionPreview(null);
  }, [conversation.id, conversation.turns.length]);

  useEffect(() => {
    const updateCapacity = () => {
      const transcriptHeight = transcriptRef.current?.clientHeight ?? 0;
      const measuredHeight = chatRef.current?.clientHeight ?? 0;
      const availableHeight =
        transcriptHeight > 0
          ? transcriptHeight
          : measuredHeight > 0
            ? measuredHeight
            : window.innerHeight;
      setQuestionRailBoundaryHeight(availableHeight);
      setQuestionRailCapacity(getQuestionRailCapacity(availableHeight));
    };

    updateCapacity();
    window.addEventListener("resize", updateCapacity);
    const resizeObserver =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(updateCapacity);
    if (chatRef.current !== null) resizeObserver?.observe(chatRef.current);
    if (transcriptRef.current !== null)
      resizeObserver?.observe(transcriptRef.current);
    if (composerFormRef.current !== null)
      resizeObserver?.observe(composerFormRef.current);

    return () => {
      window.removeEventListener("resize", updateCapacity);
      resizeObserver?.disconnect();
    };
  }, [hasTurns]);

  useEffect(() => {
    const totalQuestions = conversation.turns.length;
    if (totalQuestions === 0) {
      setQuestionWindowStart(0);
      return;
    }

    const isWindowed = totalQuestions > questionRailCapacity;
    const visibleQuestions = isWindowed
      ? Math.max(1, questionRailCapacity - 2)
      : totalQuestions;
    const activeIndex = conversation.turns.findIndex(
      (turn) => turn.id === activeQuestionId,
    );
    const centerIndex = activeIndex < 0 ? totalQuestions - 1 : activeIndex;
    setQuestionWindowStart(
      clampQuestionWindowStart(
        centerIndex - Math.floor(visibleQuestions / 2),
        totalQuestions,
        visibleQuestions,
      ),
    );
  }, [
    activeQuestionId,
    conversation.id,
    conversation.turns,
    questionRailCapacity,
  ]);

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
    if (modelMenuOpen) {
      modelMenuRef.current
        ?.querySelector<HTMLButtonElement>('[aria-checked="true"]')
        ?.focus();
    } else if (wasModelMenuOpenRef.current) {
      modelTriggerRef.current?.focus();
    }
    wasModelMenuOpenRef.current = modelMenuOpen;
  }, [modelMenuOpen]);

  useEffect(() => {
    if (!modelMenuOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (
        !modelMenuRef.current?.contains(target) &&
        !modelTriggerRef.current?.contains(target)
      ) {
        setModelMenuOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    return () =>
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
  }, [modelMenuOpen]);

  const selectedModel =
    CODEX_MODELS.find((model) => model.id === conversation.modelId) ??
    CODEX_MODELS[0]!;

  const selectedSources = sources.filter((source) =>
    conversation.selectedSourceIds.includes(source.id),
  );
  const selectedAgents = agents.filter((agent) =>
    conversation.selectedAgentIds.includes(agent.id),
  );
  const selectedSkills = skills.filter((skill) =>
    conversation.selectedSkillIds.includes(skill.id),
  );
  const isQuestionRailWindowed =
    conversation.turns.length > questionRailCapacity;
  const visibleQuestionCapacity = isQuestionRailWindowed
    ? Math.max(1, questionRailCapacity - 2)
    : conversation.turns.length;
  const normalizedQuestionWindowStart = clampQuestionWindowStart(
    questionWindowStart,
    conversation.turns.length,
    visibleQuestionCapacity,
  );
  const questionWindowEnd = Math.min(
    conversation.turns.length,
    normalizedQuestionWindowStart + visibleQuestionCapacity,
  );
  const visibleQuestionEntries = conversation.turns
    .slice(normalizedQuestionWindowStart, questionWindowEnd)
    .map((turn, offset) => ({
      index: normalizedQuestionWindowStart + offset,
      turn,
    }));
  const hiddenQuestionsBefore = normalizedQuestionWindowStart;
  const hiddenQuestionsAfter = conversation.turns.length - questionWindowEnd;
  const earlierQuestionsLabel = formatQuestionCount(
    copy.chat.showEarlierQuestion,
    copy.chat.showEarlierQuestions,
    hiddenQuestionsBefore,
  );
  const laterQuestionsLabel = formatQuestionCount(
    copy.chat.showLaterQuestion,
    copy.chat.showLaterQuestions,
    hiddenQuestionsAfter,
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
    const isComposing =
      event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229;

    if (
      event.key === "ArrowUp" &&
      event.currentTarget.value.length === 0 &&
      !isComposing
    ) {
      const previousPrompt =
        conversation.turns[conversation.turns.length - 1]?.prompt;
      if (previousPrompt !== undefined) {
        event.preventDefault();
        setMessage(previousPrompt);
      }
      return;
    }

    if (event.key !== "Enter" || event.shiftKey || isComposing) {
      return;
    }
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  };

  const fillPrompt = (prompt: string) => {
    setMessage(prompt);
    composerRef.current?.focus();
  };

  const handleTranscriptScroll = () => {
    const transcript = transcriptRef.current;
    if (transcript === null) return;

    const bottomGap =
      transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight;
    let visibleQuestionId: string | null;
    if (bottomGap <= 2) {
      visibleQuestionId =
        conversation.turns[conversation.turns.length - 1]?.id ?? null;
    } else {
      const threshold = transcript.scrollTop + transcript.clientHeight * 0.34;
      visibleQuestionId = conversation.turns[0]?.id ?? null;
      for (const turn of conversation.turns) {
        const element = turnElementsRef.current.get(turn.id);
        if (element !== undefined && element.offsetTop <= threshold) {
          visibleQuestionId = turn.id;
        }
      }
    }
    setActiveQuestionId(visibleQuestionId);
    setQuestionPreview(null);
  };

  const jumpToQuestion = (turnId: string) => {
    const turn = turnElementsRef.current.get(turnId);
    if (turn === undefined) return;
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    turn.scrollIntoView({
      behavior: reduceMotion ? "auto" : "smooth",
      block: "start",
    });
    setActiveQuestionId(turnId);
  };

  const showQuestionRailPreview = (
    previewId: string,
    content: string,
    turnId: string | null,
    trigger: HTMLButtonElement,
  ) => {
    const rect = trigger.getBoundingClientRect();
    setQuestionPreview({
      content,
      left: rect.right + 8,
      previewId,
      top: rect.top + rect.height / 2,
      turnId,
    });
  };

  const shiftQuestionWindow = (offset: number) => {
    setQuestionWindowStart((current) =>
      clampQuestionWindowStart(
        current + offset,
        conversation.turns.length,
        visibleQuestionCapacity,
      ),
    );
    setQuestionPreview(null);
  };

  const handleQuestionRailWheel = (event: ReactWheelEvent<HTMLElement>) => {
    if (!isQuestionRailWindowed || event.deltaY === 0) return;
    event.preventDefault();
    shiftQuestionWindow(
      event.deltaY > 0 ? QUESTION_RAIL_WHEEL_STEP : -QUESTION_RAIL_WHEEL_STEP,
    );
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

  const handleModelMenuKeyDown = (
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) => {
    const items = Array.from(
      modelMenuRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitemradio"]',
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
      setModelMenuOpen(false);
      return;
    }

    if (nextIndex !== null && items.length > 0) {
      event.preventDefault();
      items[nextIndex]?.focus();
    }
  };

  const composer = (
    <form
      ref={composerFormRef}
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
          role="group"
          aria-label={copy.composer.messageContext}
        >
          {selectedSources.map((source) => (
            <span
              key={`source-${source.id}`}
              className="tap-context-chip"
              data-kind="knowledge"
            >
              <BookOutlined aria-hidden="true" />
              <span title={source.name}>{source.name}</span>
              <button
                type="button"
                aria-label={`${copy.composer.remove} ${source.name}`}
                onClick={() => onToggleSource(source.id)}
              >
                <CloseOutlined aria-hidden="true" />
              </button>
            </span>
          ))}
          {selectedAgents.map((agent) => (
            <span
              key={`agent-${agent.id}`}
              className="tap-context-chip"
              data-kind="agent"
            >
              <RobotOutlined aria-hidden="true" />
              <span title={agent.name}>{agent.name}</span>
              <button
                type="button"
                aria-label={`${copy.composer.remove} ${agent.name}`}
                onClick={() => onToggleAgent(agent.id)}
              >
                <CloseOutlined aria-hidden="true" />
              </button>
            </span>
          ))}
          {selectedSkills.map((skill) => (
            <span
              key={`skill-${skill.id}`}
              className="tap-context-chip"
              data-kind="skill"
            >
              <ToolOutlined aria-hidden="true" />
              <span title={skill.name}>{skill.name}</span>
              <button
                type="button"
                aria-label={`${copy.composer.remove} ${skill.name}`}
                onClick={() => onToggleSkill(skill.id)}
              >
                <CloseOutlined aria-hidden="true" />
              </button>
            </span>
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
            onClick={() => {
              setModelMenuOpen(false);
              setMenuOpen((current) => !current);
            }}
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
                <RobotOutlined aria-hidden="true" />
                {copy.composer.useAgents}
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => openPicker("skills")}
              >
                <ToolOutlined aria-hidden="true" />
                {copy.composer.useSkills}
              </button>
            </div>
          ) : null}
        </div>
        <span>{copy.chat.sourceHint}</span>
        <div className="tap-composer-model-control">
          <button
            ref={modelTriggerRef}
            type="button"
            className="tap-model-trigger"
            aria-label={`${copy.composer.selectModel}, ${copy.composer.currentModel} ${selectedModel.label}`}
            aria-haspopup="menu"
            aria-expanded={modelMenuOpen}
            onClick={() => {
              setMenuOpen(false);
              setModelMenuOpen((current) => !current);
            }}
          >
            <span>{selectedModel.label}</span>
            <DownOutlined aria-hidden="true" />
          </button>
          {modelMenuOpen ? (
            <div
              ref={modelMenuRef}
              className="tap-model-menu"
              role="menu"
              aria-label={copy.composer.models}
              onKeyDown={handleModelMenuKeyDown}
            >
              {CODEX_MODELS.map((model) => {
                const selected = model.id === selectedModel.id;
                return (
                  <button
                    key={model.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={selected}
                    onClick={() => {
                      onModelChange(model.id);
                      setModelMenuOpen(false);
                    }}
                  >
                    <span>{model.label}</span>
                    {selected ? <CheckOutlined aria-hidden="true" /> : null}
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
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

  const previewQuestionIndex = conversation.turns.findIndex(
    (turn) => turn.id === questionPreview?.turnId,
  );

  return (
    <section
      ref={chatRef}
      className={`tap-chat ${hasTurns ? "tap-chat--active" : "tap-chat--idle"}`}
      aria-label={hasTurns ? copy.chat.assistant : copy.chat.startConversation}
      aria-hidden={isInert ? true : undefined}
      inert={isInert ? true : undefined}
    >
      {hasTurns ? (
        <div
          ref={transcriptRef}
          className="tap-chat-transcript"
          role="log"
          aria-label={copy.chat.conversation}
          aria-live="polite"
          onScroll={handleTranscriptScroll}
        >
          <div className="tap-conversation">
            {conversation.turns.map((turn) => (
              <div
                id={`tap-turn-${turn.id}`}
                ref={(element) => {
                  if (element === null) {
                    turnElementsRef.current.delete(turn.id);
                  } else {
                    turnElementsRef.current.set(turn.id, element);
                  }
                }}
                className="tap-turn"
                key={turn.id}
                lang={turn.locale === "zh" ? "zh-CN" : "en"}
              >
                <div className="tap-user-message">{turn.prompt}</div>
                <div className="tap-assistant-message">
                  {renderAssistantTurn(turn)}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="tap-chat-welcome">
          <h1>{copy.chat.heading}</h1>
          <p>{copy.chat.description}</p>
        </div>
      )}

      {hasTurns ? (
        <nav
          className="tap-question-rail"
          aria-label={copy.chat.questionNavigation}
          data-placement="left"
          data-windowed={isQuestionRailWindowed ? "true" : undefined}
          onWheel={handleQuestionRailWheel}
          style={{
            maxHeight: getQuestionRailMaximumHeight(questionRailBoundaryHeight),
            top: questionRailBoundaryHeight / 2,
          }}
        >
          {isQuestionRailWindowed ? (
            hiddenQuestionsBefore > 0 ? (
              <button
                type="button"
                className="tap-question-overflow"
                aria-describedby={
                  questionPreview?.previewId === "tap-question-preview-earlier"
                    ? "tap-question-preview-earlier"
                    : undefined
                }
                aria-label={earlierQuestionsLabel}
                onBlur={() => setQuestionPreview(null)}
                onClick={() => shiftQuestionWindow(-visibleQuestionCapacity)}
                onFocus={(event) =>
                  showQuestionRailPreview(
                    "tap-question-preview-earlier",
                    earlierQuestionsLabel,
                    null,
                    event.currentTarget,
                  )
                }
                onMouseEnter={(event) =>
                  showQuestionRailPreview(
                    "tap-question-preview-earlier",
                    earlierQuestionsLabel,
                    null,
                    event.currentTarget,
                  )
                }
                onMouseLeave={() => setQuestionPreview(null)}
              >
                <span
                  className="tap-question-overflow-marker tap-question-overflow-marker--earlier"
                  aria-hidden="true"
                />
              </button>
            ) : (
              <span
                className="tap-question-overflow-spacer"
                aria-hidden="true"
              />
            )
          ) : null}
          {visibleQuestionEntries.map(({ turn, index }) => {
            const previewId = `tap-question-preview-${turn.id}`;
            const previewVisible = questionPreview?.previewId === previewId;
            const distance =
              previewQuestionIndex < 0
                ? Number.POSITIVE_INFINITY
                : Math.abs(index - previewQuestionIndex);
            const proximity =
              distance === 0
                ? "focus"
                : distance === 1
                  ? "near-1"
                  : distance === 2
                    ? "near-2"
                    : distance === 3
                      ? "near-3"
                      : "rest";

            return (
              <button
                key={turn.id}
                type="button"
                aria-controls={`tap-turn-${turn.id}`}
                aria-current={activeQuestionId === turn.id ? "true" : undefined}
                aria-describedby={previewVisible ? previewId : undefined}
                aria-label={`${copy.chat.jumpToQuestion} ${index + 1}: ${turn.prompt}`}
                data-proximity={proximity}
                onBlur={() => setQuestionPreview(null)}
                onClick={() => jumpToQuestion(turn.id)}
                onFocus={(event) =>
                  showQuestionRailPreview(
                    previewId,
                    turn.prompt,
                    turn.id,
                    event.currentTarget,
                  )
                }
                onMouseEnter={(event) =>
                  showQuestionRailPreview(
                    previewId,
                    turn.prompt,
                    turn.id,
                    event.currentTarget,
                  )
                }
                onMouseLeave={() => setQuestionPreview(null)}
              >
                <span className="tap-question-marker" aria-hidden="true" />
              </button>
            );
          })}
          {isQuestionRailWindowed ? (
            hiddenQuestionsAfter > 0 ? (
              <button
                type="button"
                className="tap-question-overflow"
                aria-describedby={
                  questionPreview?.previewId === "tap-question-preview-later"
                    ? "tap-question-preview-later"
                    : undefined
                }
                aria-label={laterQuestionsLabel}
                onBlur={() => setQuestionPreview(null)}
                onClick={() => shiftQuestionWindow(visibleQuestionCapacity)}
                onFocus={(event) =>
                  showQuestionRailPreview(
                    "tap-question-preview-later",
                    laterQuestionsLabel,
                    null,
                    event.currentTarget,
                  )
                }
                onMouseEnter={(event) =>
                  showQuestionRailPreview(
                    "tap-question-preview-later",
                    laterQuestionsLabel,
                    null,
                    event.currentTarget,
                  )
                }
                onMouseLeave={() => setQuestionPreview(null)}
              >
                <span
                  className="tap-question-overflow-marker tap-question-overflow-marker--later"
                  aria-hidden="true"
                />
              </button>
            ) : (
              <span
                className="tap-question-overflow-spacer"
                aria-hidden="true"
              />
            )
          ) : null}
        </nav>
      ) : null}

      {questionPreview !== null ? (
        <span
          id={questionPreview.previewId}
          className="tap-question-preview"
          role="tooltip"
          style={{ left: questionPreview.left, top: questionPreview.top }}
        >
          {questionPreview.content}
        </span>
      ) : null}

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
        <AccessibleDialog
          ariaLabel={pickerConfig.title}
          className="tap-context-picker"
          initialFocusSelector="input"
          onClose={() => setPicker(null)}
          opener={addTriggerRef.current}
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
            aria-label={pickerConfig.search}
            placeholder={pickerConfig.search}
            value={pickerQuery}
            onChange={(event) => setPickerQuery(event.target.value)}
          />
          <div
            className="tap-picker-options"
            role="listbox"
            aria-multiselectable="true"
          >
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
        </AccessibleDialog>
      )}
    </section>
  );
}
