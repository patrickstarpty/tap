import { describe, expect, it } from "vitest";

import { createConversation } from "../model";
import { createInitialArtifactState } from "./fixtures";
import {
  PROTOTYPE_SNAPSHOT_VERSION,
  readPrototypeSnapshot,
  type PrototypeSnapshot,
} from "./persistence";

describe("prototype persistence", () => {
  it("restores a versioned Conversation and artifact snapshot", () => {
    const snapshot: PrototypeSnapshot = {
      version: PROTOTYPE_SNAPSHOT_VERSION,
      activeConversationId: "chat-1",
      conversations: [createConversation("chat-1")],
      artifacts: createInitialArtifactState(),
    };

    expect(readPrototypeSnapshot(JSON.stringify(snapshot))).toEqual(snapshot);
  });

  it("falls back safely for malformed or incompatible snapshots", () => {
    expect(readPrototypeSnapshot("not-json")).toBeNull();
    expect(readPrototypeSnapshot('{"version":999}')).toBeNull();
    expect(readPrototypeSnapshot(null)).toBeNull();
  });

  it("adds the default Codex model when restoring a legacy Conversation", () => {
    const legacyConversation = createConversation(
      "chat-1",
    ) as unknown as Record<string, unknown>;
    delete legacyConversation.modelId;

    const restored = readPrototypeSnapshot(
      JSON.stringify({
        version: PROTOTYPE_SNAPSHOT_VERSION,
        activeConversationId: "chat-1",
        conversations: [legacyConversation],
        artifacts: createInitialArtifactState(),
      }),
    );

    expect(restored?.conversations[0]?.modelId).toBe("gpt-5.6-sol");
  });
});
