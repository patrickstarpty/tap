import { useState } from "react";
import type { AssistantTurn } from "../model";

export function AnswerEvidence({ turn }: { turn: AssistantTurn }) {
  const [traceExpanded, setTraceExpanded] = useState(false);
  const [contextExpanded, setContextExpanded] = useState(false);
  const t = (en: string, zh: string) => (turn.locale === "zh" ? zh : en);
  if (!turn.trace) return null;
  const { searchedSources, matchedPassages, citations } = turn.trace;
  const plural = (count: number, singular: string, plural: string) =>
    count === 1 ? singular : plural;
  const summary = t(
    `Searched ${searchedSources} ${plural(searchedSources, "source", "sources")} · ${matchedPassages} ${plural(matchedPassages, "passage", "passages")} matched · ${citations} ${plural(citations, "citation", "citations")}`,
    `已检索 ${searchedSources} 份来源 · 命中 ${matchedPassages} 段 · 引用 ${citations} 处`,
  );
  const knowledgeSources = turn.sourceReferences;
  const agents = (turn.catalogReferences ?? []).filter(
    (item) => item.kind === "agent",
  );
  const skills = (turn.catalogReferences ?? []).filter(
    (item) => item.kind === "skill",
  );
  return (
    <div className="tap-answer-evidence">
      <button
        type="button"
        aria-expanded={traceExpanded}
        onClick={() => setTraceExpanded((current) => !current)}
      >
        {summary}
      </button>
      {traceExpanded ? (
        <ol className="tap-answer-trace-steps">
          <li>{t("Search", "检索")}</li>
          <li>{t("Filter", "筛选")}</li>
          <li>{t("Generate", "生成")}</li>
        </ol>
      ) : null}
      <button
        type="button"
        aria-expanded={contextExpanded}
        onClick={() => setContextExpanded((current) => !current)}
      >
        {t("Sources and configuration used", "本次使用的资料与配置")}
      </button>
      {contextExpanded ? (
        <div className="tap-answer-context">
          {knowledgeSources.length > 0 ? (
            <div role="group" aria-label={t("Knowledge sources", "知识来源")}>
              <ul>
                {knowledgeSources.map((source) => (
                  <li key={source.id}>{source.name}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {agents.length > 0 ? (
            <div role="group" aria-label={t("Agent", "Agent")}>
              <ul>
                {agents.map((item) => (
                  <li key={item.id}>{item.name}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {skills.length > 0 ? (
            <div role="group" aria-label={t("Skills", "Skills")}>
              <ul>
                {skills.map((item) => (
                  <li key={item.id}>{item.name}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
