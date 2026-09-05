import { describe, expect, it } from "vitest";

import { createConversation } from "../model";
import {
  createInitialArtifactState,
  createLifeAutomation,
  createLifeTestPlan,
} from "./fixtures";
import {
  loadPrototypeSnapshot,
  PROTOTYPE_SNAPSHOT_VERSION,
  PROTOTYPE_STORAGE_KEY,
  readPrototypeSnapshot,
  type PrototypeSnapshot,
  writePrototypeSnapshot,
} from "./persistence";

describe("prototype persistence", () => {
  it("round-trips a v2 Conversation with Tapper-linked artifacts", () => {
    const snapshot: PrototypeSnapshot = {
      version: 2,
      activeConversationId: "chat-1",
      conversations: [createConversation("chat-1")],
      artifacts: {
        automations: [
          createLifeAutomation("AUTO-TAPPER", "TP-TAPPER", "Tapper"),
        ],
        testPlans: [createLifeTestPlan("TP-TAPPER", "AUTO-TAPPER", "Tapper")],
        runs: [],
      },
    };
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };

    writePrototypeSnapshot(storage, snapshot);

    expect(PROTOTYPE_SNAPSHOT_VERSION).toBe(2);
    expect(PROTOTYPE_STORAGE_KEY).toBe("tap.prototype.workspace.v2");
    expect(values.has("tap.prototype.workspace.v1")).toBe(false);
    expect(loadPrototypeSnapshot(storage)).toEqual(snapshot);
  });

  it("ignores v1 snapshots without deleting their browser state", () => {
    const v1Serialized = JSON.stringify({
      version: 1,
      activeConversationId: "chat-1",
      conversations: [createConversation("chat-1")],
      artifacts: createInitialArtifactState(),
    });
    const values = new Map([["tap.prototype.workspace.v1", v1Serialized]]);
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
    };

    expect(readPrototypeSnapshot(v1Serialized)).toBeNull();
    expect(loadPrototypeSnapshot(storage)).toBeNull();
    expect(values.get("tap.prototype.workspace.v1")).toBe(v1Serialized);
  });

  it("falls back safely for malformed or incompatible v2 snapshots", () => {
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
