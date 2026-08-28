import { describe, expect, it } from "vitest";

import { document } from "../testing/fakeKnowledgeClient";
import {
  INITIAL_SOURCE_SELECTION,
  buildAnswerRequest,
  sourceSelectionReducer,
} from "./sourceSelection";

describe("sourceSelectionReducer", () => {
  it("keeps only ready IDs from the latest snapshot in stable sorted order", () => {
    const selected = sourceSelectionReducer(INITIAL_SOURCE_SELECTION, {
      type: "snapshotChanged",
      documents: [
        document({ documentId: "doc-b", status: "ready" }),
        document({ documentId: "doc-a", status: "ready" }),
      ],
    });
    const toggled = sourceSelectionReducer(
      sourceSelectionReducer(selected, {
        type: "toggle",
        sourceId: "doc-b",
        readyIds: ["doc-a", "doc-b"],
      }),
      {
        type: "toggle",
        sourceId: "doc-a",
        readyIds: ["doc-a", "doc-b"],
      },
    );

    expect(toggled.selectedIds).toEqual(["doc-a", "doc-b"]);
    expect(
      sourceSelectionReducer(toggled, {
        type: "snapshotChanged",
        documents: [
          document({ documentId: "doc-a", status: "deleting" }),
          document({ documentId: "doc-b", status: "ready" }),
        ],
      }).selectedIds,
    ).toEqual(["doc-b"]);
  });

  it("selects the first twenty stable-sorted unique ready IDs", () => {
    const readyIds = [
      ...Array.from(
        { length: 21 },
        (_, index) => `doc-${String(21 - index).padStart(2, "0")}`,
      ),
      "doc-01",
    ];

    const selected = sourceSelectionReducer(INITIAL_SOURCE_SELECTION, {
      type: "selectAllReady",
      readyIds,
    });

    expect(selected.selectedIds).toHaveLength(20);
    expect(selected.selectedIds[0]).toBe("doc-01");
    expect(selected.selectedIds.at(-1)).toBe("doc-20");
  });

  it("blocks non-ready and twenty-first toggles while clear resets selection", () => {
    const readyIds = Array.from(
      { length: 21 },
      (_, index) => `doc-${String(index + 1).padStart(2, "0")}`,
    );
    const full = sourceSelectionReducer(INITIAL_SOURCE_SELECTION, {
      type: "selectAllReady",
      readyIds,
    });

    expect(
      sourceSelectionReducer(full, {
        type: "toggle",
        sourceId: "doc-21",
        readyIds,
      }),
    ).toBe(full);
    expect(
      sourceSelectionReducer(full, {
        type: "toggle",
        sourceId: "not-ready",
        readyIds: [],
      }),
    ).toBe(full);
    expect(sourceSelectionReducer(full, { type: "questionSubmitted" })).toBe(
      full,
    );
    expect(sourceSelectionReducer(full, { type: "clear" }).selectedIds).toEqual(
      [],
    );
  });
});

describe("buildAnswerRequest", () => {
  it("emits only the trimmed quick/doc/scope generated request shape", () => {
    const request = buildAnswerRequest("  退款需要几人审批？  ", [
      "doc-a",
      "doc-b",
    ]);

    expect(request).toStrictEqual({
      query: "退款需要几人审批？",
      answerMode: "quick",
      sources: ["doc"],
      resourceRefs: [
        { family: "doc", sourceId: "doc-a", mode: "scope" },
        { family: "doc", sourceId: "doc-b", mode: "scope" },
      ],
    });
    expect(Object.keys(request).sort()).toEqual([
      "answerMode",
      "query",
      "resourceRefs",
      "sources",
    ]);
    expect(Object.keys(request.resourceRefs?.[0] ?? {}).sort()).toEqual([
      "family",
      "mode",
      "sourceId",
    ]);
  });

  it("uses Unicode code points and rejects empty, duplicate, or out-of-range input", () => {
    expect(() => buildAnswerRequest("   ", ["doc-a"])).toThrow();
    expect(() => buildAnswerRequest("question", [])).toThrow();
    expect(() => buildAnswerRequest("question", ["doc-a", "doc-a"])).toThrow();
    expect(() =>
      buildAnswerRequest("😀".repeat(8_000), ["doc-a"]),
    ).not.toThrow();
    expect(() => buildAnswerRequest("😀".repeat(8_001), ["doc-a"])).toThrow();
  });
});
