type RequiredField = { id: string; readbackValue: string };
const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/** Validate the actual passed AA result, never prose or a synthetic fixture value. */
export function verifyRealQuoteProof(
  outputs: unknown[],
  fields: RequiredField[],
  quoteName: string,
) {
  if (
    outputs.length !== 1 ||
    !object(outputs[0]) ||
    fields.length === 0 ||
    fields.some((field) => field.id === quoteName)
  )
    throw new Error("Missing or ambiguous actual quote output");
  const output = outputs[0];
  if (!object(output.provenance))
    throw new Error("Missing observed output provenance");
  for (const name of [...fields.map((field) => field.id), quoteName]) {
    const proof = output.provenance[name];
    if (
      !object(proof) ||
      !Number.isInteger(proof.observation_sequence) ||
      Number(proof.observation_sequence) < 1 ||
      ![
        proof.source_frame_id,
        proof.source_document_id,
        proof.source_url,
      ].every((value) => typeof value === "string" && value.length > 0)
    )
      throw new Error("Missing current observed field provenance");
    const url = new URL(String(proof.source_url));
    if (!["http:", "https:"].includes(url.protocol))
      throw new Error("Invalid observation URL");
  }
  for (const field of fields) {
    if (output[field.id] !== field.readbackValue)
      throw new Error("Reviewed input readback mismatch");
  }
  const value = output[quoteName];
  if (typeof value !== "string") throw new Error("Quote is not observed text");
  const numbers = value.replace(/,/gu, "").match(/-?\d+(?:\.\d+)?/gu) ?? [];
  if (
    numbers.length !== 1 ||
    !Number.isFinite(Number(numbers[0])) ||
    Number(numbers[0]) <= 0
  )
    throw new Error("Quote must contain exactly one finite positive amount");
}
