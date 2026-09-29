import { useRef, useState } from "react";
import { Button } from "antd";
import { AccessibleDialog } from "../../../../legacy/AccessibleDialog";
import type { AssistantTurn } from "../model";
import { AnswerEvidence } from "./AnswerEvidence";

export function KnowledgeAnswer({
  turn,
  onRetry,
  onStop,
}: {
  turn: AssistantTurn;
  onRetry: () => void;
  onStop: () => void;
}) {
  const [citation, setCitation] = useState(false);
  const opener = useRef<HTMLElement | null>(null);
  const t = (en: string, zh: string) => (turn.locale === "zh" ? zh : en);
  if (turn.answerState === "running")
    return (
      <div role="status">
        <p>{t("Searching enabled sources…", "正在检索已启用资料…")}</p>
        <Button onClick={onStop}>{t("Stop", "停止生成")}</Button>
      </div>
    );
  if (turn.answerState === "canceled" || turn.answerState === "failed")
    return (
      <div>
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
      <div>
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
  return (
    <div className="tap-knowledge-answer">
      <AnswerEvidence turn={turn} />
      <p>
        {t(
          "Block submission when health disclosure is missing. Return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED, retain entered information, and prompt the applicant to complete the disclosure before resubmitting.",
          "缺少健康告知时，应阻止提交并返回 HTTP 422 与 HEALTH_DISCLOSURE_REQUIRED。保留已填信息，提示申请人补充后再提交。",
        )}
      </p>
      <Button
        type="link"
        onClick={(event) => {
          opener.current = event.currentTarget;
          setCitation(true);
        }}
      >
        [1] {t("Health disclosure · Section 4", "健康告知 · 第 4 节")}
      </Button>
      {citation ? (
        <AccessibleDialog
          ariaLabel={t("Source citation", "原文引用")}
          className="tap-document-review tap-document-citation"
          opener={opener.current}
          onClose={() => setCitation(false)}
        >
          <header>
            <div>
              <h2>{turn.sourceReferences[0]?.name}</h2>
              <p>{t("Source version · Section 4", "来源版本 · 第 4 节")}</p>
            </div>
            <Button
              onClick={() => setCitation(false)}
              aria-label={t("Close citation", "关闭引用")}
            >
              {t("Close", "关闭")}
            </Button>
          </header>
          <article className="tap-document-original">
            <h3>{t("Health disclosure", "健康告知")}</h3>
            <mark>
              {t(
                "If disclosure is missing, block submission and return HTTP 422 with HEALTH_DISCLOSURE_REQUIRED.",
                "缺少健康告知时，阻止提交并返回 HTTP 422，错误码 HEALTH_DISCLOSURE_REQUIRED。",
              )}
            </mark>
          </article>
        </AccessibleDialog>
      ) : null}
    </div>
  );
}
