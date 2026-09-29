import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clearPrototypeFault,
  isPrototypeFaultActive,
  setPrototypeFaults,
  takePrototypeFault,
} from "./prototypeFaults";

afterEach(() => setPrototypeFaults([]));

describe("prototypeFaults", () => {
  it("reads faults injected before the page loads", async () => {
    window.__TAP_PROTOTYPE_FAULTS__ = ["upload-failed", "bogus"];
    vi.resetModules();
    const mod = await import("./prototypeFaults");

    expect(mod.isPrototypeFaultActive("upload-failed")).toBe(true);
    expect(mod.isPrototypeFaultActive("send-failed")).toBe(false);

    delete window.__TAP_PROTOTYPE_FAULTS__;
  });

  it("consumes action faults once", () => {
    setPrototypeFaults(["send-failed"]);
    expect(takePrototypeFault("send-failed")).toBe(true);
    expect(takePrototypeFault("send-failed")).toBe(false);
  });

  it("clears load faults on retry", () => {
    setPrototypeFaults(["library-load-failed"]);
    expect(isPrototypeFaultActive("library-load-failed")).toBe(true);
    clearPrototypeFault("library-load-failed");
    expect(isPrototypeFaultActive("library-load-failed")).toBe(false);
  });
});
