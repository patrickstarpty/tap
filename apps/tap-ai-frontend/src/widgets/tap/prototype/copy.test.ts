import { expect, it } from "vitest";

import { PROTOTYPE_COPY } from "./copy";

function collectStrings(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(collectStrings);
  if (value !== null && typeof value === "object") {
    return Object.values(value).flatMap(collectStrings);
  }
  return [];
}

// Task 5 removes remaining demo-only keys (e.g. `chat.answer`, the
// `library.example*`/`library.illustrative` keys) that still carry demo
// wording; once those are gone this becomes a plain `it`.
it.fails("keeps demo wording out of product copy", () => {
  const values = collectStrings(PROTOTYPE_COPY);
  for (const value of values) {
    expect(value).not.toMatch(/prototype|demo|原型|演示|示例/i);
  }
});
