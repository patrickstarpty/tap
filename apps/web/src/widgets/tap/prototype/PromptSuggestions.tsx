import { useEffect, useRef, useState, type JSX } from "react";
import { Skeleton } from "antd";

import type { PrototypeCopy } from "./copy";
import { isPrototypeFaultActive } from "./prototypeFaults";
import type { PromptSuggestion } from "./samplePromptSuggestions";

export interface PromptSuggestionsProps {
  copy: PrototypeCopy;
  suggestions: readonly PromptSuggestion[];
  onPick: (suggestion: PromptSuggestion) => void;
}

const BATCH_SIZE = 4;

function sourceAttribution(
  copy: PrototypeCopy,
  sources: readonly { id: string; name: string }[],
): string {
  const [first] = sources;
  if (!first) return "";
  if (sources.length === 1) {
    return copy.chat.promptSuggestions.basedOnOne(first.name);
  }
  return copy.chat.promptSuggestions.basedOnMany(first.name, sources.length);
}

export function PromptSuggestions({
  copy,
  suggestions,
  onPick,
}: PromptSuggestionsProps): JSX.Element | null {
  const [batchStart, setBatchStart] = useState(0);
  const previousSuggestions = useRef(suggestions);

  useEffect(() => {
    if (previousSuggestions.current !== suggestions) {
      previousSuggestions.current = suggestions;
      setBatchStart(0);
    }
  }, [suggestions]);

  if (isPrototypeFaultActive("suggestions-loading")) {
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

  if (isPrototypeFaultActive("suggestions-load-failed")) {
    return null;
  }

  if (suggestions.length === 0) {
    return null;
  }

  const visibleCount = Math.min(BATCH_SIZE, suggestions.length);
  const visible = Array.from(
    { length: visibleCount },
    (_, index) => suggestions[(batchStart + index) % suggestions.length],
  );
  const showRefresh = suggestions.length > BATCH_SIZE;

  return (
    <div className="tap-prompt-suggestions-wrap">
      <div
        className="tap-prompt-suggestions"
        role="group"
        aria-label={copy.chat.promptSuggestions.label}
      >
        {visible.map((suggestion) => (
          <button
            key={suggestion.id}
            type="button"
            className="tap-prompt-suggestion"
            onClick={() => onPick(suggestion)}
          >
            <span className="tap-prompt-suggestion-question">
              {suggestion.question}
            </span>
            <span className="tap-prompt-suggestion-source">
              {sourceAttribution(copy, suggestion.sources)}
            </span>
          </button>
        ))}
      </div>
      {showRefresh ? (
        <button
          type="button"
          className="tap-prompt-suggestions-refresh"
          onClick={() =>
            setBatchStart(
              (start) => (start + BATCH_SIZE) % suggestions.length,
            )
          }
        >
          {copy.chat.promptSuggestions.refresh}
        </button>
      ) : null}
    </div>
  );
}
