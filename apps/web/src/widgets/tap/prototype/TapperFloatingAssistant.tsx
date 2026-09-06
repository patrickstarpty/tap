import {
  ArrowRightOutlined,
  ExpandAltOutlined,
  FileTextOutlined,
  MinusOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { ContextualAssistantResponse } from "./ContextualAssistantResponse";
import { PROTOTYPE_COPY } from "./copy";
import type { FloatingAssistantContext } from "./floatingAssistantModel";
import type { Conversation, Locale } from "./model";
import "./TapperFloatingAssistant.css";

const listening = new URL(
  "../../../../assets/brand/tapper/listening/svg/launcher/tapper-listening-launcher-light.svg?no-inline",
  import.meta.url,
).href;
const aha = new URL(
  "../../../../assets/brand/tapper/aha/svg/launcher/tapper-aha-launcher-light.svg?no-inline",
  import.meta.url,
).href;
const avatar = new URL(
  "../../../../assets/brand/tapper/listening/svg/avatar/tapper-listening-avatar-color.svg?no-inline",
  import.meta.url,
).href;
const wordmark = new URL(
  "../../../../assets/brand/tapper/svg/tapper-wordmark-ink.svg?no-inline",
  import.meta.url,
).href;

const TEXT = {
  en: {
    open: "Ask Tapper",
    unread: "Tapper has a new reply",
    panel: "Tapper assistant",
    minimize: "Minimize Tapper",
    continue: "Continue in Tapper",
    context: "Current page",
    greeting: "What shall we look into?",
    intro: "Explore the current page together, one question at a time.",
    prompts: "Suggested questions",
    message: "Message Tapper",
    placeholder: "Ask about this page…",
    send: "Send message",
    prototype: "Prototype · suggestions from page data",
    preparing: "Preparing a prototype suggestion…",
    conversation: "Tapper conversation",
    earlier: "View earlier messages in Tapper",
    artifact: "Open this result in Tapper to continue editing.",
    ready: "Aha, a new reply is ready",
  },
  zh: {
    open: "问问 Tapper",
    unread: "Tapper 有新回复",
    panel: "Tapper 助手",
    minimize: "收起 Tapper",
    continue: "在 Tapper 中继续",
    context: "当前页面",
    greeting: "一起看看，哪里值得多问一步？",
    intro: "围绕当前页面，聊聊你的疑问和想法。",
    prompts: "建议提问",
    message: "向 Tapper 发送消息",
    placeholder: "关于这个页面，你想了解什么？",
    send: "发送消息",
    prototype: "交互原型 · 建议基于页面数据",
    preparing: "正在整理原型建议…",
    conversation: "Tapper 对话",
    earlier: "在 Tapper 中查看更早的消息",
    artifact: "在 Tapper 中打开此结果，继续编辑。",
    ready: "Aha，有一条新回复",
  },
} as const;

interface TapperFloatingAssistantProps {
  visible: boolean;
  context: FloatingAssistantContext;
  conversation: Conversation;
  draft: string;
  locale: Locale;
  onDraftChange: (draft: string) => void;
  onSend: (
    prompt: string,
    context: FloatingAssistantContext,
    conversationId: string,
    locale: Locale,
  ) => string;
  onContinue: (context: FloatingAssistantContext) => void;
}

export function TapperFloatingAssistant({
  visible,
  context,
  conversation,
  draft,
  locale,
  onDraftChange,
  onSend,
  onContinue,
}: TapperFloatingAssistantProps) {
  const text = TEXT[locale];
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(false);
  const [pending, setPending] = useState<{ id: string; prompt: string } | null>(
    null,
  );
  const launcherRef = useRef<HTMLButtonElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const responseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastTurnId = conversation.turns.at(-1)?.id;
  const previousTurnId = useRef(lastTurnId);
  const presentationState = useRef({ open, visible });
  const readyTurns = conversation.turns.filter(
    (turn) => turn.id !== pending?.id,
  );
  const shownTurns = readyTurns.slice(-5);

  useEffect(() => {
    presentationState.current = { open, visible };
  }, [open, visible]);

  useEffect(() => {
    if (lastTurnId !== previousTurnId.current) {
      if (visible && !open) setUnread(true);
      previousTurnId.current = lastTurnId;
    }
    if (open || !visible) setUnread(false);
    if (!visible) setOpen(false);
  }, [lastTurnId, open, visible]);

  useEffect(() => {
    // Full Tapper already shows the saved reply. Do not replay its notification
    // after returning, or carry its presentation into a different conversation.
    if (responseTimer.current !== null) clearTimeout(responseTimer.current);
    responseTimer.current = null;
    setPending(null);
    setUnread(false);
  }, [conversation.id, visible]);

  useEffect(() => {
    if (open && visible) composerRef.current?.focus();
  }, [open, visible]);

  useEffect(() => {
    const transcript = transcriptRef.current;
    if (open && transcript) transcript.scrollTop = transcript.scrollHeight;
  }, [lastTurnId, pending, open]);

  useEffect(
    () => () => {
      if (responseTimer.current !== null) clearTimeout(responseTimer.current);
    },
    [],
  );

  const minimize = () => {
    setOpen(false);
    launcherRef.current?.focus();
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const prompt = draft.trim();
    if (!prompt || pending !== null) return;
    onDraftChange("");
    const id = onSend(prompt, context, conversation.id, locale);
    setPending({ id, prompt });
    // Save the turn immediately. Only its floating presentation is delayed,
    // so the prototype can demonstrate an unread reply without reordering history.
    responseTimer.current = setTimeout(() => {
      setPending(null);
      const current = presentationState.current;
      if (current.visible && !current.open) setUnread(true);
      responseTimer.current = null;
    }, 600);
    composerRef.current?.focus();
  };

  if (!visible) return null;

  return (
    <div className="tap-floating-assistant">
      {open ? (
        <section
          id="tap-floating-panel"
          role="dialog"
          aria-modal="false"
          aria-label={text.panel}
          className="tap-floating-panel"
          onKeyDown={(event) => {
            if (event.key === "Escape" && !event.nativeEvent.isComposing) {
              event.stopPropagation();
              minimize();
            }
          }}
        >
          <header className="tap-floating-header">
            <div className="tap-floating-identity">
              <img className="tap-floating-avatar" src={avatar} alt="" />
              <h2 aria-label="Tapper">
                <img className="tap-floating-wordmark" src={wordmark} alt="" />
              </h2>
            </div>
            <div className="tap-floating-tools">
              <button
                type="button"
                aria-label={text.continue}
                title={text.continue}
                onClick={() => onContinue(context)}
              >
                <ExpandAltOutlined aria-hidden="true" />
              </button>
              <button
                type="button"
                aria-label={text.minimize}
                title={text.minimize}
                onClick={minimize}
              >
                <MinusOutlined aria-hidden="true" />
              </button>
            </div>
          </header>

          <details key={context.key} className="tap-floating-context">
            <summary>
              <FileTextOutlined aria-hidden="true" />
              <span>
                <small>{text.context}</small>
                <strong>{context.label}</strong>
              </span>
            </summary>
            <p>{context.summary}</p>
            <ul>
              {context.facts.map((fact) => (
                <li key={fact}>{fact}</li>
              ))}
            </ul>
          </details>

          <div ref={transcriptRef} className="tap-floating-body">
            {shownTurns.length === 0 && pending === null ? (
              <div className="tap-floating-welcome">
                <h3>{text.greeting}</h3>
                <p>{text.intro}</p>
                <div
                  className="tap-floating-prompts"
                  role="group"
                  aria-label={text.prompts}
                >
                  {context.prompts.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => {
                        onDraftChange(prompt);
                        composerRef.current?.focus();
                      }}
                    >
                      <span>{prompt}</span>
                      <ArrowRightOutlined aria-hidden="true" />
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div role="log" aria-label={text.conversation} aria-live="polite">
                {readyTurns.length > shownTurns.length ? (
                  <button
                    className="tap-floating-history"
                    type="button"
                    onClick={() => onContinue(context)}
                  >
                    {text.earlier}
                  </button>
                ) : null}
                {shownTurns.map((turn) => (
                  <div
                    key={turn.id}
                    className="tap-floating-turn"
                    lang={turn.locale === "zh" ? "zh-CN" : "en"}
                  >
                    <p className="tap-floating-question">{turn.prompt}</p>
                    {turn.prototypeReply ? (
                      <ContextualAssistantResponse turn={turn} />
                    ) : turn.intent === "answer" ? (
                      <p>{PROTOTYPE_COPY[turn.locale].chat.answer}</p>
                    ) : (
                      <button
                        className="tap-floating-history"
                        type="button"
                        onClick={() => onContinue(context)}
                      >
                        {text.artifact}
                      </button>
                    )}
                  </div>
                ))}
                {pending !== null ? (
                  <div className="tap-floating-turn">
                    <p className="tap-floating-question">{pending.prompt}</p>
                    <p role="status" className="tap-floating-preparing">
                      {text.preparing}
                    </p>
                  </div>
                ) : null}
              </div>
            )}
          </div>

          <form className="tap-floating-composer" onSubmit={submit}>
            <label
              className="tapper-visually-hidden"
              htmlFor="tap-floating-message"
            >
              {text.message}
            </label>
            <textarea
              ref={composerRef}
              id="tap-floating-message"
              rows={2}
              value={draft}
              placeholder={text.placeholder}
              onChange={(event) => onDraftChange(event.target.value)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !event.shiftKey &&
                  !event.nativeEvent.isComposing &&
                  event.nativeEvent.keyCode !== 229
                ) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
            />
            <div className="tap-floating-composer-footer">
              <small>{text.prototype}</small>
              <button
                type="submit"
                aria-label={text.send}
                title={text.send}
                disabled={!draft.trim() || pending !== null}
              >
                <SendOutlined aria-hidden="true" />
              </button>
            </div>
          </form>
        </section>
      ) : null}
      <button
        ref={launcherRef}
        type="button"
        className="tap-floating-launcher"
        aria-label={unread ? text.unread : text.open}
        aria-expanded={open}
        aria-controls={open ? "tap-floating-panel" : undefined}
        title={open ? text.minimize : unread ? text.unread : text.open}
        onClick={() => (open ? minimize() : setOpen(true))}
      >
        <img src={unread ? aha : listening} alt="" width="56" height="56" />
        {unread ? (
          <span className="tap-floating-unread" aria-hidden="true" />
        ) : null}
      </button>
      <span role="status" className="tapper-visually-hidden">
        {unread ? text.ready : ""}
      </span>
    </div>
  );
}
