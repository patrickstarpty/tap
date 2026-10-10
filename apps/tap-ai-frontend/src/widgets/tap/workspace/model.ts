import type { RetrievalAnswerResponse } from "../../../features/knowledge/api/types";
import type { InsightsExplanationResult } from "../../../features/conversations/api/client";
import type { GraphContextSummary } from "../../../features/conversations/model/stream";

export type Locale = "en" | "zh";

export type ModelId = string;

// Pre-catalog placeholder: the chat composer replaces it with the catalog's
// `defaultAlias` (TAPPER_DEFAULT_CHAT_MODEL) once the catalog loads.
export const PENDING_MODEL_ID: ModelId = "";

// Mirrors the backend catalog name rule.
export function isModelId(value: unknown): value is ModelId {
  return (
    typeof value === "string" &&
    value.length <= 128 &&
    /^[a-z0-9]+(?:[.-][a-z0-9]+)*$/.test(value)
  );
}

export type ProductModule =
  "tapper" | "agents" | "skills" | "library" | "test-management";

export type TapperSurface = "chat" | "agents" | "skills" | "library";

export type AssistantIntent = "answer";

export type CatalogKind = "agent" | "skill";

export type CatalogOrigin = "built-in" | "custom";

export type LibrarySourceOrigin = "knowledge-base";

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
  graphContext?: GraphContextSummary | null;
  traceId?: string | null;
  attempt?: number;
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
    modelId: options.modelId ?? PENDING_MODEL_ID,
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
