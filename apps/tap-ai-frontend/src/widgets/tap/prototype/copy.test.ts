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

it("keeps demo wording out of product copy", () => {
  const values = collectStrings(PROTOTYPE_COPY);
  for (const value of values) {
    expect(value).not.toMatch(/prototype|demo|原型|演示|示例/i);
  }
});
