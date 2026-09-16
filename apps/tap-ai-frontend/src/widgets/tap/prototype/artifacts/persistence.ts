import {
  DEFAULT_MODEL_ID,
  isModelId,
  type Conversation,
  type LibrarySource,
} from "../model";

export const PROTOTYPE_SNAPSHOT_VERSION = 2 as const;
export const PROTOTYPE_STORAGE_KEY = "tap.ai.workspace.v2";

export interface PrototypeSnapshot {
  version: typeof PROTOTYPE_SNAPSHOT_VERSION;
  activeConversationId: string;
  conversations: readonly Conversation[];
  library?: {
    open: boolean;
    examplesLoaded: boolean;
    fwdLoaded: boolean;
    localSources: readonly Pick<LibrarySource, "id" | "name" | "type">[];
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isStringArray(value: unknown): value is string[] {
  return (
    Array.isArray(value) && value.every((item) => typeof item === "string")
  );
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
      !Array.isArray(value.conversations)
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
      if (
        !isRecord(conversation) ||
        typeof conversation.id !== "string" ||
        typeof conversation.title !== "string" ||
        !Array.isArray(conversation.turns) ||
        !isStringArray(conversation.selectedSourceIds) ||
        !isStringArray(conversation.selectedAgentIds) ||
        !isStringArray(conversation.selectedSkillIds)
      )
        throw new Error("Invalid fixture conversation");
      const modelId = isModelId(conversation.modelId)
        ? conversation.modelId
        : DEFAULT_MODEL_ID;
      const turns = conversation.turns.map((turn) => {
        if (
          !isRecord(turn) ||
          typeof turn.id !== "string" ||
          typeof turn.prompt !== "string" ||
          !["answer", "test-plan", "automation"].includes(
            String(turn.intent),
          ) ||
          !["en", "zh"].includes(String(turn.locale)) ||
          !Array.isArray(turn.sourceReferences) ||
          !turn.sourceReferences.every(
            (source) =>
              isRecord(source) &&
              typeof source.id === "string" &&
              typeof source.name === "string" &&
              ["knowledge-base", "page-local"].includes(String(source.origin)),
          )
        )
          throw new Error("Invalid fixture turn");
        return {
          id: turn.id,
          prompt: turn.prompt,
          intent: turn.intent,
          locale: turn.locale,
          modelId: isModelId(turn.modelId) ? turn.modelId : modelId,
          sourceReferences: turn.sourceReferences.map(
            ({ id, name, origin }) => ({ id, name, origin }),
          ),
          ...(Array.isArray(turn.catalogReferences)
            ? {
                catalogReferences: turn.catalogReferences
                  .filter(
                    (item) =>
                      isRecord(item) &&
                      typeof item.id === "string" &&
                      typeof item.name === "string" &&
                      ["agent", "skill"].includes(String(item.kind)),
                  )
                  .map(({ id, kind, name }) => ({ id, kind, name })),
              }
            : {}),
        };
      });
      return {
        id: conversation.id,
        title: conversation.title,
        selectedSourceIds: conversation.selectedSourceIds,
        selectedAgentIds: conversation.selectedAgentIds,
        selectedSkillIds: conversation.selectedSkillIds,
        modelId,
        turns,
      };
    });
    const library = isRecord(value.library)
      ? {
          open: value.library.open === true,
          examplesLoaded: value.library.examplesLoaded === true,
          fwdLoaded: value.library.fwdLoaded === true,
          localSources: Array.isArray(value.library.localSources)
            ? value.library.localSources
                .filter(
                  (source) =>
                    isRecord(source) &&
                    typeof source.id === "string" &&
                    typeof source.name === "string" &&
                    typeof source.type === "string",
                )
                .map(({ id, name, type }) => ({ id, name, type }))
            : [],
        }
      : undefined;
    return {
      version: PROTOTYPE_SNAPSHOT_VERSION,
      activeConversationId: value.activeConversationId,
      conversations,
      ...(library ? { library } : { library: undefined }),
    } as unknown as PrototypeSnapshot;
  } catch {
    return null;
  }
}

export function loadPrototypeSnapshot(
  storage: Pick<Storage, "getItem">,
): PrototypeSnapshot | null {
  try {
    return readPrototypeSnapshot(
      storage.getItem(PROTOTYPE_STORAGE_KEY) ??
        storage.getItem("tap.prototype.workspace.v2"),
    );
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
