import { describe, expect, it } from "vitest";

import { formatTraceSummary } from "./formatTraceSummary";
import type { TurnTraceSummary } from "../api/client";

const summary = (overrides: Partial<TurnTraceSummary>): TurnTraceSummary => ({
  totalDurationMs: 1500,
  inputTokens: 120,
  outputTokens: 340,
  costUsd: "0.0234",
  costIncomplete: false,
  requestedModels: ["gpt-4o"],
  upstreamModels: ["gpt-4o-2024"],
  attemptCount: 1,
  ...overrides,
});

describe("formatTraceSummary", () => {
  it("formats full summary", () => {
    const text = formatTraceSummary(summary({}), "en");

    expect(text).toContain("1.5s");
    expect(text).toContain("120/340 tokens");
    expect(text).toContain("$0.0234");
    expect(text).toContain("gpt-4o");
    expect(text).toContain("gpt-4o-2024");
    expect(text).toContain("→");
  });

  it("shows cost unknown when all costs missing", () => {
    const text = formatTraceSummary(summary({ costUsd: null }), "en");
    expect(text).toContain("cost unknown");

    const zh = formatTraceSummary(summary({ costUsd: null }), "zh");
    expect(zh).toContain("成本未知");
  });

  it("marks partial cost", () => {
    const text = formatTraceSummary(
      summary({ costUsd: "0.0100", costIncomplete: true }),
      "en",
    );
    expect(text).toContain("$0.0100+");
    expect(text).toContain("(partly unknown)");

    const zh = formatTraceSummary(
      summary({ costUsd: "0.0100", costIncomplete: true }),
      "zh",
    );
    expect(zh).toContain("$0.0100+");
    expect(zh).toContain("（部分成本未知）");
  });
});
