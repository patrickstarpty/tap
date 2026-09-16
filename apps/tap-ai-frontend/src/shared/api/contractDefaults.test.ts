import { describe, expect, it } from "vitest";

import type { components } from "./generated/schema";

describe("generated request defaults", () => {
  it("keeps defaulted retrieval fields optional for the browser request", () => {
    const request = {
      answerMode: "quick",
      query: "Tapper 如何约束回答来源？",
      resourceRefs: [
        {
          family: "doc",
          mode: "scope",
          sourceId: "doc-1",
        },
      ],
    } satisfies components["schemas"]["RetrievalAnswerRequest"];

    expect(request).toEqual({
      answerMode: "quick",
      query: "Tapper 如何约束回答来源？",
      resourceRefs: [
        {
          family: "doc",
          mode: "scope",
          sourceId: "doc-1",
        },
      ],
    });
    expect(request).not.toHaveProperty("requestedEnvironment");
    expect(request).not.toHaveProperty("requestedCorpusVersion");
    expect(request).not.toHaveProperty("topK");
  });
});
