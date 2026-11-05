import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WORKSPACE_COPY } from "../../copy";
import { RunValue } from "./memberCells";
import type { RuntimeStatus } from "./RuntimeBadge";

const copy = WORKSPACE_COPY.zh;

function text(value: string | null, runtimeStatus: RuntimeStatus) {
  const { container } = render(
    <RunValue value={value} ctx={{ copy, runtimeStatus }} />,
  );
  return container.textContent;
}

describe("RunValue", () => {
  it("marks values by runtime status, not by whether a value is missing", () => {
    // A legitimately empty value (never ran) is just a dash.
    expect(text(null, "ok")).toBe("—");
    expect(text(null, "syncing")).toBe("—");
    // Syncing numbers may trail: annotate the number itself.
    expect(text("12", "syncing")).toBe("12运行数据同步中");
    expect(text("12", "ok")).toBe("12");
    // Unavailable run data is unknown, not zero.
    expect(text(null, "unavailable")).toBe("—运行数据暂不可用");
  });
});
