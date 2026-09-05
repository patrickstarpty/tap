import {
  DEFAULT_CODEX_MODEL_ID,
  isCodexModelId,
  type Conversation,
} from "../model";
import type { ArtifactState } from "./model";

export const PROTOTYPE_SNAPSHOT_VERSION = 2 as const;
export const PROTOTYPE_STORAGE_KEY = "tap.prototype.workspace.v2";

export interface PrototypeSnapshot {
  version: typeof PROTOTYPE_SNAPSHOT_VERSION;
  activeConversationId: string;
  conversations: readonly Conversation[];
  artifacts: ArtifactState;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function readPrototypeSnapshot(
  serialized: string | null,
): PrototypeSnapshot | null {
  if (serialized === null) return null;
  try {
    const value: unknown = JSON.parse(serialized);
    if (!isRecord(value) || value.version !== PROTOTYPE_SNAPSHOT_VERSION) {
      return null;
    }
    if (
      typeof value.activeConversationId !== "string" ||
      !Array.isArray(value.conversations) ||
      !isRecord(value.artifacts) ||
      !Array.isArray(value.artifacts.automations) ||
      !Array.isArray(value.artifacts.testPlans) ||
      !Array.isArray(value.artifacts.runs)
    ) {
      return null;
    }
    const hasActiveConversation = value.conversations.some(
      (conversation) =>
        isRecord(conversation) &&
        conversation.id === value.activeConversationId,
    );
    if (!hasActiveConversation) return null;
    const conversations = value.conversations.map((conversation) => {
      if (!isRecord(conversation)) return conversation;
      const modelId = isCodexModelId(conversation.modelId)
        ? conversation.modelId
        : DEFAULT_CODEX_MODEL_ID;
      const turns = Array.isArray(conversation.turns)
        ? conversation.turns.map((turn) =>
            isRecord(turn)
              ? {
                  ...turn,
                  modelId: isCodexModelId(turn.modelId)
                    ? turn.modelId
                    : modelId,
                }
              : turn,
          )
        : conversation.turns;
      return {
        ...conversation,
        modelId,
        ...(Array.isArray(conversation.turns) ? { turns } : {}),
      };
    });
    return { ...value, conversations } as unknown as PrototypeSnapshot;
  } catch {
    return null;
  }
}

export function loadPrototypeSnapshot(
  storage: Pick<Storage, "getItem">,
): PrototypeSnapshot | null {
  try {
    return readPrototypeSnapshot(storage.getItem(PROTOTYPE_STORAGE_KEY));
  } catch {
    return null;
  }
}

export function writePrototypeSnapshot(
  storage: Pick<Storage, "setItem">,
  snapshot: PrototypeSnapshot,
): void {
  try {
    storage.setItem(PROTOTYPE_STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // The prototype remains usable when storage is unavailable or full.
  }
}
