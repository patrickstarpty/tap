import { expect, test } from "@playwright/test";
import { verifyRealQuoteProof } from "./browser-core-real-proof";
const proof = {
  observation_sequence: 3,
  source_frame_id: "f1",
  source_document_id: "d1",
  source_url: "https://example.org/quote",
};
const fields = [{ id: "age", readbackValue: "30" }];
const valid = () => ({
  age: "30",
  quote: "HK$ 120.50",
  provenance: { age: { ...proof }, quote: { ...proof } },
});
test("accept actual quote plus reviewed field observations", () => {
  expect(() => verifyRealQuoteProof([valid()], fields, "quote")).not.toThrow();
});
for (const amount of ["", "0", "-5", "HK$ 120 or 240", "Infinity"]) {
  test(`reject nonpositive or ambiguous quote ${amount}`, () => {
    expect(() =>
      verifyRealQuoteProof([{ ...valid(), quote: amount }], fields, "quote"),
    ).toThrow();
  });
}
test("reject missing evidence, wrong inputs and duplicate outputs", () => {
  expect(() =>
    verifyRealQuoteProof([{ ...valid(), provenance: {} }], fields, "quote"),
  ).toThrow();
  expect(() =>
    verifyRealQuoteProof([{ ...valid(), age: "31" }], fields, "quote"),
  ).toThrow();
  expect(() =>
    verifyRealQuoteProof([valid(), valid()], fields, "quote"),
  ).toThrow();
  expect(() => verifyRealQuoteProof([valid()], [], "quote")).toThrow();
});
