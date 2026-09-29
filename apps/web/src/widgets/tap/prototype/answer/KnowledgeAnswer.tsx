import { Button } from "antd";
import type { AssistantTurn } from "../model";
import { AnswerEvidence } from "./AnswerEvidence";
import type { OpenCitation } from "./CitationPanel";

function CitationButtons({
  turn,
  onOpenCitation,
}: {
  turn: AssistantTurn;
  onOpenCitation: (citation: OpenCitation) => void;
}) {
  return (
    <>
      {turn.sourceReferences.map((source, index) => (
        <Button
          key={source.id}
          type="link"
          onClick={() =>
            onOpenCitation({ turnId: turn.id, index: index + 1, source })
          }
        >
          [{index + 1}] {source.name}
        </Button>
      ))}
    </>
  );
}

export function KnowledgeAnswer({
  turn,
  onRetry,
  onStop,
  onOpenCitation,
}: {
  turn: AssistantTurn;
  onRetry: () => void;
  onStop: () => void;
  onOpenCitation: (citation: OpenCitation) => void;
}) {
  const t = (en: string, zh: string) => (turn.locale === "zh" ? zh : en);
  if (turn.answerState === "queued")
    return (
      <div className="tap-answer-state" data-state="queued" role="status">
        <p>{t("Waiting to start…", "等待开始…")}</p>
        <Button onClick={onStop}>{t("Stop", "停止生成")}</Button>
      </div>
    );
  if (turn.answerState === "running")
    return (
      <div className="tap-answer-state" data-state="running" role="status">
        <p>
          {t(
            `Using ${turn.sourceReferences.length} sources · Generating answer…`,
            `使用 ${turn.sourceReferences.length} 份来源 · 正在生成回答…`,
          )}
        </p>
        <Button onClick={onStop}>{t("Stop", "停止生成")}</Button>
      </div>
    );
  if (turn.answerState === "canceled" || turn.answerState === "failed")
    return (
      <div className="tap-answer-state" data-state="failed">
        <p>
          {turn.answerState === "canceled"
            ? t("Generation stopped.", "已停止生成。")
            : t(
                "The answer could not be generated. Please try again.",
                "回答生成失败，请重试。",
              )}
        </p>
        <Button onClick={onRetry}>{t("Retry", "重试")}</Button>
      </div>
    );
  if (turn.answerState === "insufficient")
    return (
      <div className="tap-answer-state" data-state="insufficient">
        <p>
          {t(
            "The available sources do not contain enough evidence to answer this question.",
            "现有资料不足以支持这个问题的结论。",
          )}
        </p>
        <p>
          {t(
            "Choose a relevant source, narrow your question, or add supporting documents to Library.",
            "请选择相关来源、缩小问题范围，或在知识库补充资料。",
          )}
        </p>
      </div>
    );
  if (turn.answerState === "conflict")
    return (
      <div className="tap-answer-state" data-state="conflict">
        <p>
          {t(
            "The two sources reach different conclusions.",
            "两份资料结论不一致。",
          )}
        </p>
        <CitationButtons turn={turn} onOpenCitation={onOpenCitation} />
      </div>
    );
  if (turn.answerState === "source-changed")
    return (
      <div className="tap-answer-state" data-state="source-changed">
        <p>
          {t(
            "Sources were updated while answering. Please resubmit.",
            "回答期间资料已更新，请重新提交。",
          )}
        </p>
        <Button onClick={onRetry}>{t("Resubmit", "重新提交")}</Button>
      </div>
    );
  if (turn.answerState === "interrupted")
    return (
      <div className="tap-answer-state" data-state="interrupted">
        <p>
          {t(
            "Conversation updates stopped. Your message is saved.",
            "对话更新已中断，你的消息已保存。",
          )}
        </p>
        <Button onClick={onRetry}>{t("Retry", "重试")}</Button>
      </div>
    );
  return (
    <div className="tap-knowledge-answer">
      <AnswerEvidence turn={turn} />
      {turn.retrievalLimited ? (
        <p className="tap-answer-retrieval-limited" role="status">
          {t(
            "Some sources could not be searched. This answer uses the remaining sources.",
            "部分来源暂时无法检索，回答仅基于其余来源。",
          )}
        </p>
      ) : null}
      <p>
        {t(
          "Block submission when health disclosure is missing. Return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED, retain entered information, and prompt the applicant to complete the disclosure before resubmitting.",
          "缺少健康告知时，应阻止提交并返回 HTTP 422 与 HEALTH_DISCLOSURE_REQUIRED。保留已填信息，提示申请人补充后再提交。",
        )}
      </p>
      <CitationButtons turn={turn} onOpenCitation={onOpenCitation} />
    </div>
  );
}
