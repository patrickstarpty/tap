import { describe, expect, it } from "vitest";

import { aiAssetPresentation } from "./aiAssets";

describe("approved AI asset presentation", () => {
  it("uses user-facing meaning instead of an integrity digest", () => {
    expect(aiAssetPresentation("agent", "zh")).toEqual({
      description: "受控知识问答智能体",
      instructions: "由服务端批准并锁定版本",
    });
    expect(JSON.stringify(aiAssetPresentation("skill", "en"))).not.toContain(
      "sha256:",
    );
  });
});
