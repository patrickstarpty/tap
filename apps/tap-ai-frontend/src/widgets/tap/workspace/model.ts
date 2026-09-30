import type { RetrievalAnswerResponse } from "../../../features/knowledge/api/types";
import type { InsightsExplanationResult } from "../../../features/conversations/api/client";

export type Locale = "en" | "zh";

export type ModelId = string;

export const DEFAULT_MODEL_ID: ModelId = "tapper-chat";

export function isModelId(value: unknown): value is ModelId {
  return typeof value === "string" && /^[a-z][a-z0-9._-]{0,127}$/.test(value);
}

export type ProductModule =
  "tapper" | "agents" | "skills" | "library" | "test-management";

export type TapperSurface = "chat" | "agents" | "skills" | "library";

export type AssistantIntent = "answer" | "test-plan" | "automation";

export type CatalogKind = "agent" | "skill";

export type CatalogOrigin = "built-in" | "custom";

export type LibrarySourceOrigin = "knowledge-base" | "page-local";

export interface AssistantSourceReference {
  id: string;
  name: string;
  origin: LibrarySourceOrigin;
}

export interface AssistantTurn {
  id: string;
  intent: AssistantIntent;
  locale: Locale;
  modelId: ModelId;
  prompt: string;
  sourceReferences: readonly AssistantSourceReference[];
  response?: RetrievalAnswerResponse | null;
  insightsExplanation?: InsightsExplanationResult;
  insightsQueryId?: string;
  status?:
    "queued" | "running" | "completed" | "abstained" | "canceled" | "failed";
  error?: string | null;
  evidenceStatus?: "loading" | "ready" | "missing" | "error";
  contextLabels?: readonly string[];
  inputSnapshotDigest?: string;
  answerEvidenceSnapshotDigest?: string | null;
  agentRevisionId?: string | null;
  skillRevisionIds?: readonly string[];
  catalogReferences?: readonly Pick<CatalogItem, "id" | "kind" | "name">[];
  pageContext?: {
    label: string;
    summary: string;
    facts: readonly string[];
  };
  contextualReply?: {
    text: string;
    suggestions: readonly string[];
  };
}

export interface Conversation {
  id: string;
  title: string;
  turns: readonly AssistantTurn[];
  modelId: ModelId;
  selectedSourceIds: readonly string[];
  selectedAgentIds: readonly string[];
  selectedSkillIds: readonly string[];
}

export interface CatalogItem {
  id: string;
  kind: CatalogKind;
  origin: CatalogOrigin;
  name: string;
  description: string;
  instructions: string;
}

export interface LibrarySource {
  downloadUrl?: string;
  preview?: { imageUrl?: string; text?: string };
  id: string;
  name: string;
  origin: LibrarySourceOrigin;
  type: string;
  status: "ready" | "processing" | "failed";
  description: string;
}

export interface CreateConversationOptions {
  title?: string;
  modelId?: ModelId;
  selectedSourceIds?: readonly string[];
  selectedAgentIds?: readonly string[];
  selectedSkillIds?: readonly string[];
}

export function createConversation(
  id: string,
  options: CreateConversationOptions = {},
): Conversation {
  return {
    id,
    title: options.title ?? "New chat",
    turns: [],
    modelId: options.modelId ?? DEFAULT_MODEL_ID,
    selectedSourceIds: [...(options.selectedSourceIds ?? [])],
    selectedAgentIds: [...(options.selectedAgentIds ?? [])],
    selectedSkillIds: [...(options.selectedSkillIds ?? [])],
  };
}

export function appendTurn(
  conversation: Conversation,
  turn: AssistantTurn,
): Conversation {
  return {
    ...conversation,
    title:
      conversation.turns.length === 0 && conversation.title === "New chat"
        ? turn.prompt
        : conversation.title,
    turns: [...conversation.turns, turn],
    selectedSourceIds: [],
    selectedAgentIds: [],
    selectedSkillIds: [],
  };
}
