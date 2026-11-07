import { expect, it } from "vitest";
import { browserSendInput, startUrlFromGoal } from "./browserInput";

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
