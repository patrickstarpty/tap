import { useEffect, useId, useRef, useState } from "react";
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
  const upButtonRef = useRef<HTMLButtonElement>(null);
  const downButtonRef = useRef<HTMLButtonElement>(null);
  const [value, setValue] = useState<FeedbackValue | null>(initialValue);
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Which button (if any) should regain focus once the in-flight request
  // settles. 👍/👎 are `disabled` while `busy` (fix round 1's guard
  // against rapid clicks), and a browser blurs an element the instant it
  // is disabled — so calling `.focus()` synchronously from a mutation's
  // `onSuccess` (which TanStack Query v5 runs before `isPending` flips
  // back to false) is a silent no-op and focus falls through to <body>.
  // Requesting focus via this flag and applying it in the effect below,
  // which only fires once `busy` is actually false, fixes that race for
  // Submit, Skip and a retract alike.
  const [pendingFocus, setPendingFocus] = useState<"up" | "down" | null>(null);
  const feedback = useTurnFeedback(projectId, conversationId);
  const busy = feedback.isPending;

  useEffect(() => {
    if (busy || pendingFocus === null) return;
    (pendingFocus === "up" ? upButtonRef : downButtonRef).current?.focus();
    setPendingFocus(null);
  }, [busy, pendingFocus]);

  const closeComment = () => {
    setCommentOpen(false);
    setComment("");
    setPendingFocus("down");
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
    // 👍 never opens the comment box, so the click's own button is always
    // the right place for focus to land back on once busy clears.
    setPendingFocus("up");
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
      // The box stays closed throughout, so — like 👍 — focus belongs
      // back on 👎 itself once the request settles.
      setCommentOpen(false);
      setComment("");
      setPendingFocus("down");
      save(null, null, snapshot);
      return;
    }
    // Opening the comment box hands focus to its (autoFocus) textarea, so
    // no `pendingFocus` request here — reclaiming 👎 once this PUT
    // settles would otherwise yank focus away from the box the user is
    // about to type into.
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
        ref={upButtonRef}
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
