import { describe, expect, it } from "vitest";

import { bucketAxis, fillBuckets } from "./buckets";

describe("bucketAxis", () => {
  it("lists every day of the period", () => {
    const axis = bucketAxis({ from: "2026-10-01", to: "2026-10-04" }, "day");
    expect(axis.map((b) => b.key)).toEqual([
      "2026-10-01",
      "2026-10-02",
      "2026-10-03",
      "2026-10-04",
    ]);
    expect(axis[0].label).toBe("2026-10-01");
  });

  it("starts weeks on Monday and clamps partial first and last weeks", () => {
    // 2026-10-07 is a Wednesday; 2026-10-21 is a Wednesday.
    const axis = bucketAxis({ from: "2026-10-07", to: "2026-10-21" }, "week");
    expect(axis.map((b) => b.key)).toEqual([
      "2026-10-05",
      "2026-10-12",
      "2026-10-19",
    ]);
    expect(axis[0].label).toBe("2026-10-07 – 2026-10-11");
    expect(axis[1].label).toBe("2026-10-12 – 2026-10-18");
    expect(axis[2].label).toBe("2026-10-19 – 2026-10-21");
  });

  it("uses the first of each month across a year end", () => {
    const axis = bucketAxis({ from: "2025-11-15", to: "2026-02-10" }, "month");
    expect(axis.map((b) => b.key)).toEqual([
      "2025-11-01",
      "2025-12-01",
      "2026-01-01",
      "2026-02-01",
    ]);
    expect(axis[0].label).toBe("2025-11-15 – 2025-11-30");
    expect(axis[1].label).toBe("2025-12-01 – 2025-12-31");
    expect(axis[3].label).toBe("2026-02-01 – 2026-02-10");
  });

  it("is empty for a reversed range", () => {
    expect(bucketAxis({ from: "2026-10-05", to: "2026-10-01" }, "day")).toEqual(
      [],
    );
  });
});

describe("fillBuckets", () => {
  it("fills missing buckets with zero in axis order", () => {
    const axis = bucketAxis({ from: "2026-10-01", to: "2026-10-03" }, "day");
    const filled = fillBuckets(axis, [
      { bucketStart: "2026-10-03", value: 4 },
      { bucketStart: "2026-10-01", value: 1 },
    ]);
    expect(filled.map((p) => p.value)).toEqual([1, 0, 4]);
  });
});
