import type { AssistantTurn } from "./model";

export function ContextualAssistantResponse({ turn }: { turn: AssistantTurn }) {
  const zh = turn.locale === "zh";
  return (
    <div className="tap-contextual-response">
      <p className="tap-contextual-response-origin">
        {zh
          ? "原型建议 · 基于页面数据"
          : "Prototype suggestion · based on page data"}
      </p>
      <p>{turn.prototypeReply?.text}</p>
      {turn.prototypeReply?.suggestions.length ? (
        <ul>
          {turn.prototypeReply.suggestions.map((suggestion) => (
            <li key={suggestion}>{suggestion}</li>
          ))}
        </ul>
      ) : null}
      {turn.pageContext ? (
        <details className="tap-contextual-evidence">
          <summary>
            {zh ? "提问时的页面信息" : "Page information at send time"}
          </summary>
          <strong>{turn.pageContext.label}</strong>
          <p>{turn.pageContext.summary}</p>
          <ul>
            {turn.pageContext.facts.map((fact) => (
              <li key={fact}>{fact}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
