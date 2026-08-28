import { Alert, Button, Input } from "antd";
import { useState, type FormEvent } from "react";

import { COPY } from "../copy";

export function QuestionComposer({
  pending,
  selectedCount,
  onSubmit,
}: {
  pending: boolean;
  selectedCount: number;
  onSubmit: (question: string) => void;
}) {
  const [question, setQuestion] = useState("");
  const trimmedLength = Array.from(question.trim()).length;
  const tooLong = trimmedLength > 8_000;
  const empty = trimmedLength === 0;
  const validationMessage = tooLong
    ? COPY.queryTooLong
    : empty && question.length > 0
      ? COPY.queryRequired
      : null;

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending || selectedCount === 0 || empty || tooLong) return;
    onSubmit(question);
  };

  return (
    <form className="athena-question-composer" onSubmit={submit}>
      <label htmlFor="athena-question">{COPY.questionLabel}</label>
      <Input.TextArea
        id="athena-question"
        value={question}
        aria-describedby="athena-question-help"
        placeholder={COPY.questionPlaceholder}
        rows={5}
        disabled={pending}
        onChange={(event) => setQuestion(event.target.value)}
        onKeyDown={(event) => {
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
        }}
      />
      <div className="athena-composer-footer" id="athena-question-help">
        <span>{`${trimmedLength.toLocaleString("zh-CN")} / 8,000`}</span>
        <Button
          type="primary"
          htmlType="submit"
          autoInsertSpace={false}
          loading={pending}
          disabled={pending || selectedCount === 0 || empty || tooLong}
        >
          {COPY.ask}
        </Button>
      </div>
      {validationMessage !== null ? (
        <Alert type="warning" showIcon title={validationMessage} />
      ) : null}
    </form>
  );
}
