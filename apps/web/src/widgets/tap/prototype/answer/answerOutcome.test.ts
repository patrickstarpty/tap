import { describe, expect, it } from "vitest";
import { decideAnswerOutcome } from "./answerOutcome";
import type { LibrarySource } from "../model";

function source(
  overrides: Partial<LibrarySource> & Pick<LibrarySource, "id" | "name">,
): LibrarySource {
  return {
    origin: "knowledge-base",
    type: "PDF",
    status: "ready",
    description: "",
    ...overrides,
  };
}

const beneficiaryDocx = source({
  id: "sample-beneficiary",
  name: "Beneficiary change workflow.docx",
});
const beneficiaryCases = source({
  id: "sample-test-cases",
  name: "Beneficiary test cases.xlsx",
});
const underwritingGuide = source({
  id: "underwriting-v12",
  name: "Life underwriting guide · v1.2.md",
});

describe("decideAnswerOutcome", () => {
  it("detects conflicting beneficiary sources", () => {
    const result = decideAnswerOutcome("What changes a beneficiary?", [
      beneficiaryDocx,
      beneficiaryCases,
    ]);
    expect(result.outcome).toBe("conflict");
    expect(result.evidence).toEqual([beneficiaryDocx, beneficiaryCases]);
  });

  it("does not flag a conflict with only one matching beneficiary source", () => {
    const result = decideAnswerOutcome("What changes a beneficiary?", [
      beneficiaryDocx,
    ]);
    expect(result.outcome).not.toBe("conflict");
  });

  it("keeps the underwriting completion rule", () => {
    const completed = decideAnswerOutcome(
      "Summarize the health disclosure rules",
      [underwritingGuide],
    );
    expect(completed.outcome).toBe("completed");
    expect(completed.evidence).toEqual([underwritingGuide]);

    const insufficient = decideAnswerOutcome(
      "What is the claims turnaround?",
      [underwritingGuide],
    );
    expect(insufficient.outcome).toBe("insufficient");
  });

  it("flags partially indexed sources", () => {
    const partial = source({
      id: "underwriting-evidence-pdf",
      name: "Underwriting evidence.pdf",
      partiallyIndexed: true,
    });
    const result = decideAnswerOutcome(
      "Summarize the health disclosure rules",
      [underwritingGuide, partial],
    );
    expect(result.retrievalLimited).toBe(true);
  });

  it("does not flag retrieval as limited without a partially indexed source", () => {
    const result = decideAnswerOutcome(
      "Summarize the health disclosure rules",
      [underwritingGuide],
    );
    expect(result.retrievalLimited).toBe(false);
  });
});
