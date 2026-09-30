import type { LibrarySource, Locale } from "./model";

export interface PromptSuggestion {
  readonly id: string;
  readonly question: string;
  readonly sources: readonly { id: string; name: string }[];
}

interface SamplePromptSuggestion {
  readonly id: string;
  readonly question: Record<Locale, string>;
  readonly sourceIds: readonly string[];
}

const SAMPLE_PROMPT_SUGGESTIONS: readonly SamplePromptSuggestion[] = [
  {
    id: "underwriting-evidence-over-60",
    question: {
      en: "What evidence is required for applicants over 60 in the life underwriting guide?",
      zh: "核保指引中，60 岁以上投保人需要提供哪些证明材料？",
    },
    sourceIds: ["underwriting-v12"],
  },
  {
    id: "health-disclosure-mandatory-fields",
    question: {
      en: "Which fields are mandatory on the approved health disclosure policy?",
      zh: "已批准的健康告知政策中，哪些字段是必填的？",
    },
    sourceIds: ["health-disclosure-approved"],
  },
  {
    id: "premium-rate-lookup",
    question: {
      en: "How do I look up the premium rate for a 45-year-old non-smoker in the premium rates sheet?",
      zh: "如何在费率表中查询 45 岁非吸烟者的保费费率？",
    },
    sourceIds: ["premium-rates-xlsx"],
  },
  {
    id: "underwriting-rules-cover-guide",
    question: {
      en: "Do the underwriting test rules cover every decision boundary in the life underwriting guide?",
      zh: "核保测试规则是否覆盖了核保指引中的所有决策边界？",
    },
    sourceIds: ["sample-underwriting", "underwriting-v12"],
  },
  {
    id: "beneficiary-workflow-test-coverage",
    question: {
      en: "Which steps of the beneficiary change workflow are covered by the beneficiary test cases?",
      zh: "受益人变更流程中的哪些步骤已被受益人测试用例覆盖？",
    },
    sourceIds: ["sample-beneficiary", "sample-test-cases"],
  },
  {
    id: "premium-rates-underwriting-cross-check",
    question: {
      en: "Do the premium rates align with the risk classes defined in the life underwriting guide?",
      zh: "费率表中的费率是否与核保指引中定义的风险等级一致？",
    },
    sourceIds: ["premium-rates-xlsx", "underwriting-v12"],
  },
];

export function availablePromptSuggestions(
  locale: Locale,
  readySources: readonly LibrarySource[],
): readonly PromptSuggestion[] {
  const readyNameById = new Map(
    readySources
      .filter((source) => source.status === "ready")
      .map((source) => [source.id, source.name] as const),
  );

  const result: PromptSuggestion[] = [];
  for (const suggestion of SAMPLE_PROMPT_SUGGESTIONS) {
    const sources: { id: string; name: string }[] = [];
    let allReady = true;
    for (const sourceId of suggestion.sourceIds) {
      const name = readyNameById.get(sourceId);
      if (name === undefined) {
        allReady = false;
        break;
      }
      sources.push({ id: sourceId, name });
    }
    if (!allReady) continue;
    result.push({
      id: suggestion.id,
      question: suggestion.question[locale],
      sources,
    });
  }
  return result;
}
