import { useId, useRef, useState } from "react";
import { Button, Input } from "antd";
import {
  DislikeFilled,
  DislikeOutlined,
  LikeFilled,
  LikeOutlined,
} from "@ant-design/icons";
import "./AnswerFeedback.css";

export type FeedbackRating = "up" | "down";

export interface FeedbackValue {
  rating: FeedbackRating;
  comment: string | null;
}

export function AnswerFeedback({
  locale,
  value,
  onChange,
}: {
  locale: "en" | "zh";
  value: FeedbackValue | null;
  onChange: (next: FeedbackValue | null) => void;
}) {
  const t = (en: string, zh: string) => (locale === "zh" ? zh : en);
  const commentInputId = useId();
  const downButtonRef = useRef<HTMLButtonElement>(null);
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState("");

  const closeComment = () => {
    setCommentOpen(false);
    setComment("");
    downButtonRef.current?.focus();
  };

  const handleUp = () => {
    setCommentOpen(false);
    setComment("");
    onChange(value?.rating === "up" ? null : { rating: "up", comment: null });
  };

  const handleDown = () => {
    if (value?.rating === "down") {
      // Already rated down: reopen the comment editor without sending a
      // change or discarding the previously saved comment.
      setComment(value.comment ?? "");
      setCommentOpen(true);
      return;
    }
    onChange({ rating: "down", comment: null });
    setComment("");
    setCommentOpen(true);
  };

  const submitComment = () => {
    const trimmed = comment.trim();
    onChange({ rating: "down", comment: trimmed.length > 0 ? trimmed : null });
    closeComment();
  };

  return (
    <div className="tap-answer-feedback">
      <Button
        type="text"
        size="small"
        aria-label={t("Helpful", "有帮助")}
        aria-pressed={value?.rating === "up"}
        icon={value?.rating === "up" ? <LikeFilled /> : <LikeOutlined />}
        onClick={handleUp}
      />
      <Button
        ref={downButtonRef}
        type="text"
        size="small"
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
            <Button type="primary" size="small" onClick={submitComment}>
              {t("Submit", "提交")}
            </Button>
            <Button type="text" size="small" onClick={closeComment}>
              {t("Skip", "跳过")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
