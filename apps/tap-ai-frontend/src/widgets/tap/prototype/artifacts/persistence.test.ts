import { describe, expect, it } from "vitest";

import { createConversation } from "../model";
import {
  loadPrototypeSnapshot,
  PROTOTYPE_SNAPSHOT_VERSION,
  PROTOTYPE_STORAGE_KEY,
  readPrototypeSnapshot,
  type PrototypeSnapshot,
  writePrototypeSnapshot,
} from "./persistence";

describe("prototype persistence", () => {
  it("imports only validated AI conversation fields from legacy snapshots", () => {
    const legacy = {
      version: 2,
      activeConversationId: "chat-1",
      conversations: [
        {
          ...createConversation("chat-1"),
          turns: [
            {
              id: "turn-1",
              intent: "automation",
              locale: "en",
              modelId: "tapper-chat",
              prompt: "Generate automation",
              sourceReferences: [],
              automationWorkflow: { automationId: "AUTO-101" },
            },
          ],
        },
      ],
    };
    const imported = readPrototypeSnapshot(JSON.stringify(legacy));
    expect(imported?.conversations[0]?.turns[0]?.prompt).toBe(
      "Generate automation",
    );
    expect(imported?.conversations[0]?.turns[0]).not.toHaveProperty(
      "automationWorkflow",
    );
    expect(
      readPrototypeSnapshot(
        JSON.stringify({
          ...legacy,
          conversations: [{ id: "chat-1", turns: [null] }],
        }),
      ),
    ).toBeNull();
  });

  it("isolates AI fixture saves from the legacy TAP browser snapshot", () => {
    const legacy = JSON.stringify({
      version: 2,
      activeConversationId: "chat-1",
      conversations: [createConversation("chat-1")],
      artifacts: { automations: [{ id: "retained-tap-automation" }] },
    });
    const values = new Map([["tap.prototype.workspace.v2", legacy]]);
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };
    const restored = loadPrototypeSnapshot(storage);
    expect(restored?.activeConversationId).toBe("chat-1");
    expect(restored).not.toHaveProperty("artifacts");
    writePrototypeSnapshot(storage, {
      ...restored!,
      activeConversationId: "chat-2",
      conversations: [createConversation("chat-2")],
    });
    expect(values.get("tap.prototype.workspace.v2")).toBe(legacy);
    expect(loadPrototypeSnapshot(storage)?.activeConversationId).toBe("chat-2");
  });

  it("round-trips a fixture Conversation independently of TAP artifacts", () => {
    const snapshot: PrototypeSnapshot = {
      version: 2,
      activeConversationId: "chat-1",
      conversations: [createConversation("chat-1")],
    };
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };

    writePrototypeSnapshot(storage, snapshot);

    expect(PROTOTYPE_SNAPSHOT_VERSION).toBe(2);
    expect(JSON.parse(values.get(PROTOTYPE_STORAGE_KEY)!)).toEqual(snapshot);
    expect(values.has("tap.prototype.workspace.v1")).toBe(false);
    expect(loadPrototypeSnapshot(storage)).toEqual(snapshot);
  });

  it("retains library flags and source records without storing the bundled demo content", () => {
    const snapshot: PrototypeSnapshot = {
      version: 2,
      activeConversationId: "chat-1",
      conversations: [createConversation("chat-1")],
      library: {
        open: true,
        examplesLoaded: true,
        fwdLoaded: true,
        localSources: [
          { id: "local-source-4", name: "My notes.md", type: "MD" },
        ],
      },
    };
    expect(readPrototypeSnapshot(JSON.stringify(snapshot))).toEqual(snapshot);
    expect(
      readPrototypeSnapshot(
        JSON.stringify({
          ...snapshot,
          library: {
            open: "true",
            examplesLoaded: false,
            fwdLoaded: true,
            localSources: [
              null,
              { id: "bad", name: 123 },
              ...snapshot.library!.localSources,
            ],
          },
        }),
      )?.library,
    ).toEqual({ ...snapshot.library, open: false, examplesLoaded: false });
    expect(
      readPrototypeSnapshot(JSON.stringify({ ...snapshot, library: "invalid" }))
        ?.library,
    ).toBeUndefined();
  });

  it("ignores v1 snapshots without deleting their browser state", () => {
    const v1Serialized = JSON.stringify({
      version: 1,
      activeConversationId: "chat-1",
      conversations: [createConversation("chat-1")],
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

  it("adds the governed default alias when restoring a legacy Conversation", () => {
    const legacyConversation = createConversation(
      "chat-1",
    ) as unknown as Record<string, unknown>;
    delete legacyConversation.modelId;

    const restored = readPrototypeSnapshot(
      JSON.stringify({
        version: PROTOTYPE_SNAPSHOT_VERSION,
        activeConversationId: "chat-1",
        conversations: [legacyConversation],
      }),
    );

    expect(restored?.conversations[0]?.modelId).toBe("tapper-chat");
  });
});
