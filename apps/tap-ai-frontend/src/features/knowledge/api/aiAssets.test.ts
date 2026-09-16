import { describe, expect, it } from "vitest";

import { aiAssetPresentation } from "./aiAssets";

describe("approved AI asset presentation", () => {
  it("uses user-facing meaning instead of an integrity digest", () => {
    expect(
      aiAssetPresentation("agent", "zh", [
        "knowledge.search",
        "knowledge.answer",
      ]),
    ).toEqual({
      description: "检索知识来源 · 回答知识问题",
      instructions: "由服务端批准并锁定版本",
    });
    expect(
      JSON.stringify(aiAssetPresentation("skill", "en", ["knowledge.answer"])),
    ).not.toContain("sha256:");
  });
});
