import { Skeleton } from "antd";
import { useEffect, useRef, useState, type JSX } from "react";

import type { PromptSuggestionItem } from "../../../features/knowledge/api/types";
import type { WorkspaceCopy } from "./copy";

export type PromptSuggestionsState =
  | { kind: "loading" }
  | { kind: "ready"; items: readonly PromptSuggestionItem[] }
  | { kind: "hidden" };

export interface PromptSuggestionsProps {
  copy: WorkspaceCopy;
  state: PromptSuggestionsState;
  onPick(item: PromptSuggestionItem): void;
}

const BATCH_SIZE = 4;

function sourceAttribution(
  copy: WorkspaceCopy,
  sources: PromptSuggestionItem["sources"],
): string {
  const [first] = sources;
  if (first === undefined) return "";
  if (sources.length === 1) {
    return copy.chat.promptSuggestions.basedOnOne(first.name);
  }
  return copy.chat.promptSuggestions.basedOnMany(first.name, sources.length);
}

export function PromptSuggestions({
  copy,
  state,
  onPick,
}: PromptSuggestionsProps): JSX.Element | null {
  const [batchStart, setBatchStart] = useState(0);
  const previousItems = useRef<PromptSuggestionsState>(state);

  useEffect(() => {
    if (
      previousItems.current.kind !== "ready" ||
      state.kind !== "ready" ||
      previousItems.current.items !== state.items
    ) {
      setBatchStart(0);
    }
    previousItems.current = state;
  }, [state]);

  if (state.kind === "loading") {
    return (
      <div
        className="tap-prompt-suggestions"
        aria-busy="true"
        aria-label={copy.chat.promptSuggestions.label}
      >
        {Array.from({ length: BATCH_SIZE }, (_, index) => (
          <Skeleton.Button key={index} active block />
        ))}
      </div>
    );
  }

  if (state.kind === "hidden" || state.items.length === 0) {
    return null;
  }

  const items = state.items;
  const visibleCount = Math.min(BATCH_SIZE, items.length);
  const visible = Array.from(
    { length: visibleCount },
    (_, index) => items[(batchStart + index) % items.length],
  );
  const showRefresh = items.length > BATCH_SIZE;

  return (
    <div className="tap-prompt-suggestions-wrap">
      <div
        className="tap-prompt-suggestions"
        role="group"
        aria-label={copy.chat.promptSuggestions.label}
      >
        {visible.map((item) => (
          <button
            key={item.id}
            type="button"
            className="tap-prompt-suggestion"
            onClick={() => onPick(item)}
          >
            <span className="tap-prompt-suggestion-question">
              {item.question}
            </span>
            <span className="tap-prompt-suggestion-source">
              {sourceAttribution(copy, item.sources)}
            </span>
          </button>
        ))}
      </div>
      {showRefresh ? (
        <button
          type="button"
          className="tap-prompt-suggestions-refresh"
          onClick={() =>
            setBatchStart((start) => (start + BATCH_SIZE) % items.length)
          }
        >
          {copy.chat.promptSuggestions.refresh}
        </button>
      ) : null}
    </div>
  );
}
