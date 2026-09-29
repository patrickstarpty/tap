import { DEFAULT_CODEX_MODEL_ID } from "./model";
import type { AssistantTurn, Conversation } from "./model";

interface SampleTurnSpec {
  answerState: "completed" | "insufficient";
  prompt: string;
  sourceId: string;
  sourceName: string;
}

/** Twelve prior chats seeded into the prototype so history paging and search have content. */
const SAMPLE_TURN_SPECS: readonly SampleTurnSpec[] = [
  {
    answerState: "completed",
    prompt: "What is the maximum face amount we can issue without a medical exam?",
    sourceId: "underwriting-v12",
    sourceName: "Life underwriting guide · v1.2.md",
  },
  {
    answerState: "completed",
    prompt: "How do I process a beneficiary change request?",
    sourceId: "sample-beneficiary",
    sourceName: "Beneficiary change workflow.docx",
  },
  {
    answerState: "insufficient",
    prompt: "What is the surrender charge schedule for the new annuity product?",
    sourceId: "sample-beneficiary",
    sourceName: "Beneficiary change workflow.docx",
  },
  {
    answerState: "completed",
    prompt: "What test cases cover the premium grace period rule?",
    sourceId: "underwriting-v12",
    sourceName: "Life underwriting guide · v1.2.md",
  },
  {
    answerState: "completed",
    prompt: "Summarize the approval flow for high-risk applicants.",
    sourceId: "underwriting-v12",
    sourceName: "Life underwriting guide · v1.2.md",
  },
  {
    answerState: "insufficient",
    prompt: "What automation coverage exists for the claims intake API?",
    sourceId: "sample-beneficiary",
    sourceName: "Beneficiary change workflow.docx",
  },
  {
    answerState: "completed",
    prompt: "Which disclosures are required before binding a policy?",
    sourceId: "underwriting-v12",
    sourceName: "Life underwriting guide · v1.2.md",
  },
  {
    answerState: "completed",
    prompt: "How long does a beneficiary change take to take effect?",
    sourceId: "sample-beneficiary",
    sourceName: "Beneficiary change workflow.docx",
  },
  {
    answerState: "completed",
    prompt: "What test plan validates the underwriting rate lookup?",
    sourceId: "underwriting-v12",
    sourceName: "Life underwriting guide · v1.2.md",
  },
  {
    answerState: "insufficient",
    prompt: "What is the refund policy for a lapsed rider?",
    sourceId: "sample-beneficiary",
    sourceName: "Beneficiary change workflow.docx",
  },
  {
    answerState: "completed",
    prompt: "Explain how retrieval-limited answers are flagged to reviewers.",
    sourceId: "underwriting-v12",
    sourceName: "Life underwriting guide · v1.2.md",
  },
  {
    answerState: "completed",
    prompt: "What documents support a contested beneficiary claim?",
    sourceId: "sample-beneficiary",
    sourceName: "Beneficiary change workflow.docx",
  },
] as const;

function sampleTurn(index: number, spec: SampleTurnSpec): AssistantTurn {
  const turn: AssistantTurn = {
    id: `sample-turn-${index + 1}`,
    intent: "answer",
    locale: "en",
    modelId: DEFAULT_CODEX_MODEL_ID,
    prompt: spec.prompt,
    sourceReferences: [
      { id: spec.sourceId, name: spec.sourceName, origin: "knowledge-base" },
    ],
    answerState: spec.answerState,
    ...(spec.answerState === "completed"
      ? {
          trace: {
            searchedSources: 1,
            matchedPassages: 3,
            citations: 1,
          },
        }
      : {}),
  };
  return turn;
}

/** Older, previously created chats shown after the fresh chat and any new chats in history. */
export const SAMPLE_CONVERSATIONS: readonly Conversation[] =
  SAMPLE_TURN_SPECS.map((spec, index) => ({
    id: `sample-chat-${index + 1}`,
    title: spec.prompt,
    turns: [sampleTurn(index, spec)],
    modelId: DEFAULT_CODEX_MODEL_ID,
    selectedSourceIds: [],
    selectedAgentIds: [],
    selectedSkillIds: [],
  }));
