import type { LibrarySource } from "../model";

export type AnswerOutcome = "completed" | "insufficient" | "conflict";

const BENEFICIARY_SOURCE_NAME = /beneficiary/i;
const BENEFICIARY_PROMPT = /beneficiar|受益人/i;
const UNDERWRITING_SOURCE_NAME = /underwriting|健康|核保/i;
const UNDERWRITING_PROMPT = /health|disclosure|underwriting|健康|告知|核保/i;

export function decideAnswerOutcome(
  prompt: string,
  sources: readonly LibrarySource[],
): {
  outcome: AnswerOutcome;
  evidence: readonly LibrarySource[];
  retrievalLimited: boolean;
} {
  const retrievalLimited = sources.some((source) => source.partiallyIndexed);
  const beneficiarySources = sources.filter((source) =>
    BENEFICIARY_SOURCE_NAME.test(source.name),
  );
  if (beneficiarySources.length >= 2 && BENEFICIARY_PROMPT.test(prompt)) {
    return { outcome: "conflict", evidence: beneficiarySources, retrievalLimited };
  }
  const evidence = sources.filter((source) =>
    UNDERWRITING_SOURCE_NAME.test(source.name),
  );
  const outcome: AnswerOutcome =
    evidence.length > 0 && UNDERWRITING_PROMPT.test(prompt)
      ? "completed"
      : "insufficient";
  return { outcome, evidence, retrievalLimited };
}
