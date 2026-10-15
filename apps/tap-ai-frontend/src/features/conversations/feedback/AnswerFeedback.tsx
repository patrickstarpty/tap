import { useId, useRef, useState } from "react";
import { Button, Input } from "antd";
import {
  DislikeFilled,
  DislikeOutlined,
  LikeFilled,
  LikeOutlined,
} from "@ant-design/icons";

import { useTurnFeedback } from "../api/queries";
import type { AnswerFeedbackRequest } from "../api/client";
import "./AnswerFeedback.css";

export type FeedbackRating = "up" | "down";

export interface FeedbackValue {
  rating: FeedbackRating;
  comment: string | null;
}

/**
 * Owner-only 👍/👎 rating on a completed Turn's answer, calling the
 * feedback API (Task 4) on every real state transition: none/down→up and
 * none/up→down PUT the new rating (a down also opens an optional comment
 * box); re-clicking the already-selected button DELETEs (retracts) it;
 * Submit PUTs the trimmed comment (or null); Skip sends nothing. A failed
 * request reverts to the state before that click and shows an error
 * message. Mirrors the design baseline in
 * `apps/web/src/widgets/tap/prototype/answer/AnswerFeedback.tsx`, but that
 * component is onChange-controlled and design-only — this one owns the
 * network calls directly.
 */
export function AnswerFeedback({
  projectId,
  conversationId,
  turnId,
  locale,
  initialValue,
}: {
  projectId: string | null;
  conversationId: string;
  turnId: string;
  locale: "en" | "zh";
  initialValue: FeedbackValue | null;
}) {
  const t = (en: string, zh: string) => (locale === "zh" ? zh : en);
  const commentInputId = useId();
  const downButtonRef = useRef<HTMLButtonElement>(null);
  const [value, setValue] = useState<FeedbackValue | null>(initialValue);
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const feedback = useTurnFeedback(projectId, conversationId);
  const busy = feedback.isPending;

  const closeComment = () => {
    setCommentOpen(false);
    setComment("");
    downButtonRef.current?.focus();
  };

  // Saves a rating/retraction and, on failure, restores the *entire* prior
  // UI snapshot (rating, comment box open/closed, draft text) — not just
  // the rating — so a failed 👎 (which optimistically opens the comment
  // box) closes it again instead of leaving a now-unrated box open.
  const save = (
    requestValue: AnswerFeedbackRequest | null,
    next: FeedbackValue | null,
    snapshot: {
      value: FeedbackValue | null;
      commentOpen: boolean;
      comment: string;
    },
  ) => {
    setError(null);
    setValue(next);
    feedback.mutate(
      { turnId, value: requestValue },
      {
        onError: () => {
          setValue(snapshot.value);
          setCommentOpen(snapshot.commentOpen);
          setComment(snapshot.comment);
          setError(
            t(
              "Could not save your feedback. Try again.",
              "反馈保存失败，请重试。",
            ),
          );
        },
      },
    );
  };

  const handleUp = () => {
    if (busy) return;
    const snapshot = { value, commentOpen, comment };
    setCommentOpen(false);
    setComment("");
    if (value?.rating === "up") {
      save(null, null, snapshot);
      return;
    }
    save(
      { rating: "up", comment: null },
      { rating: "up", comment: null },
      snapshot,
    );
  };

  const handleDown = () => {
    if (busy) return;
    const snapshot = { value, commentOpen, comment };
    if (value?.rating === "down") {
      // Already rated down: a second click retracts it, symmetric with 👍.
      setCommentOpen(false);
      setComment("");
      save(null, null, snapshot);
      return;
    }
    setComment("");
    setCommentOpen(true);
    save(
      { rating: "down", comment: null },
      { rating: "down", comment: null },
      snapshot,
    );
  };

  const submitComment = () => {
    if (busy) return;
    const previousValue = value;
    const trimmed = comment.trim();
    const nextComment = trimmed.length > 0 ? trimmed : null;
    const next = { rating: "down" as const, comment: nextComment };
    setError(null);
    setValue(next);
    feedback.mutate(
      { turnId, value: next },
      {
        onSuccess: () => closeComment(),
        onError: () => {
          // Unlike a failed rating click, a failed Submit must not lose the
          // user's draft or close the box — only the rating value (which
          // was already saved as "down" before the box opened) reverts, so
          // the user can edit and retry.
          setValue(previousValue);
          setError(
            t(
              "Could not save your feedback. Try again.",
              "反馈保存失败，请重试。",
            ),
          );
        },
      },
    );
  };

  return (
    <div className="tap-answer-feedback" aria-busy={busy}>
      <Button
        type="text"
        size="small"
        disabled={busy}
        aria-label={t("Helpful", "有帮助")}
        aria-pressed={value?.rating === "up"}
        icon={value?.rating === "up" ? <LikeFilled /> : <LikeOutlined />}
        onClick={handleUp}
      />
      <Button
        ref={downButtonRef}
        type="text"
        size="small"
        disabled={busy}
        aria-label={t("Not helpful", "没帮助")}
        aria-pressed={value?.rating === "down"}
        icon={
          value?.rating === "down" ? <DislikeFilled /> : <DislikeOutlined />
        }
        onClick={handleDown}
      />
      {commentOpen ? (
        <div className="tap-answer-feedback-comment">
          <label htmlFor={commentInputId}>
            {t("Add a comment (optional)", "补充说明（可选）")}
          </label>
          <Input.TextArea
            id={commentInputId}
            maxLength={500}
            autoFocus
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
          <div className="tap-answer-feedback-comment-actions">
            <Button
              type="primary"
              size="small"
              disabled={busy}
              onClick={submitComment}
            >
              {t("Submit", "提交")}
            </Button>
            <Button type="text" size="small" onClick={closeComment}>
              {t("Skip", "跳过")}
            </Button>
          </div>
        </div>
      ) : null}
      {error === null ? null : (
        <p role="alert" className="tap-answer-feedback-error">
          {error}
        </p>
      )}
    </div>
  );
}
