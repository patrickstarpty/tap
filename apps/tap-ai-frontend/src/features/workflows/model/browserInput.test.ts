import { expect, it } from "vitest";
import {
  acceptanceContractFromDraft,
  acceptanceDraftFromContract,
  browserAcceptanceReady,
  type BrowserAcceptanceDraft,
  browserSendInput,
  startUrlFromGoal,
} from "./browserInput";

it("extracts an explicit HTTP start URL from a natural-language goal", () => {
  expect(
    startUrlFromGoal(
      "Find Ada at https://example.org/quotes?region=west, then report the price",
    ),
  ).toBe("https://example.org/quotes?region=west");
  expect(startUrlFromGoal("Find Ada's quote")).toBe("");
  expect(startUrlFromGoal("Read file:///etc/passwd")).toBe("");
});

it("builds a browser request from goal and optional plain input without asking for JSON", () => {
  expect(
    browserSendInput({
      goal: "Find Ada at https://example.org/quotes",
      startUrl: "",
      notes: "Customer Ada",
      expectedResult: "",
      sourceTaskId: "task-blocked",
    }),
  ).toEqual({
    startUrl: "https://example.org/quotes",
    inputNotes: "Customer Ada",
    sourceTaskId: "task-blocked",
  });
});

it("snapshots reviewed per-field values and preserves the original contract for new runs", () => {
  const draft: BrowserAcceptanceDraft = {
    reviewed: true,
    fields: [
      {
        id: "start_date",
        value: "2026-11-11",
        readbackValue: "11/11/2026",
        attribute: "value",
        targetTexts: ["Start date"],
        scopeText: "Booking",
      },
      {
        id: "travelers",
        value: "2",
        readbackValue: "2 adults",
        attribute: "text",
        targetTexts: ["Travelers"],
      },
    ],
  };
  const contract = acceptanceContractFromDraft(draft)!;
  const request = browserSendInput({
    goal: "Book",
    startUrl: "https://example.test",
    notes: "",
    expectedResult: "",
    sourceTaskId: null,
    acceptanceContract: contract,
  });
  draft.fields[0].value = "mutated";
  draft.fields[0].targetTexts[0] = "mutated";
  const restored = acceptanceDraftFromContract(request.acceptanceContract);
  expect(restored.fields[0].value).toBe("2026-11-11");
  expect(restored.fields[0].readbackValue).toBe("11/11/2026");
  expect(restored.fields[1].readbackValue).toBe("2 adults");
  const nextRun = browserSendInput({
    goal: "Continue",
    startUrl: "https://example.test",
    notes: "",
    expectedResult: "",
    sourceTaskId: "blocked-1",
    acceptanceContract: acceptanceContractFromDraft(restored),
  });
  expect(nextRun.acceptanceContract).toEqual(request.acceptanceContract);
  restored.fields[0].value = "new";
  expect(nextRun.acceptanceContract?.fields[0].value).toBe("2026-11-11");
});

it("rejects unreviewed, invalid, duplicate or non-ASCII fields without converting freeform notes", () => {
  const field = {
    id: "customer",
    value: "Ada",
    readbackValue: "Ada",
    attribute: "value" as const,
    targetTexts: ["Customer"],
  };
  expect(() =>
    acceptanceContractFromDraft({ reviewed: false, fields: [field] }),
  ).toThrow(/Review/);
  for (const fields of [
    [{ ...field, id: "客户" }],
    [field, field],
    [{ ...field, readbackValue: "x".repeat(4001) }],
    [{ ...field, id: "final_url" }],
    [{ ...field, scopeText: " " }],
    [{ ...field, targetTexts: ["x".repeat(201)] }],
    [{ ...field, targetTexts: [] }],
  ]) {
    expect(browserAcceptanceReady({ reviewed: true, fields })).toBe(false);
  }
  expect(
    acceptanceContractFromDraft({ reviewed: false, fields: [] }),
  ).toBeUndefined();
});

it("allows explicitly reviewed empty values", () => {
  expect(
    browserAcceptanceReady({
      reviewed: true,
      fields: [
        {
          id: "empty-input",
          value: "",
          readbackValue: "",
          attribute: "value",
          targetTexts: ["Optional details"],
        },
      ],
    }),
  ).toBe(true);
});
