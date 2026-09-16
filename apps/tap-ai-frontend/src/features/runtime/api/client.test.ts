import { describe, expect, it, vi } from "vitest";
import { createRuntimeClient } from "./client";

const mode = {
  mode: "validation",
  identityMode: "validation",
  projectId: "project-server",
  actorId: "actor-server",
};
describe("runtime client", () => {
  it("resolves the trusted runtime endpoint", async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(mode));
    expect(await createRuntimeClient({ fetch: fetcher }).getMode()).toEqual(
      mode,
    );
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/runtime-mode");
  });
  it.each(["projectId", "actorId"] as const)(
    "accepts the 128-character %s boundary",
    async (field) => {
      const value = { ...mode, [field]: "a".repeat(128) };
      const fetcher = vi.fn().mockResolvedValue(Response.json(value));
      expect(await createRuntimeClient({ fetch: fetcher }).getMode()).toEqual(
        value,
      );
    },
  );
  it.each(["projectId", "actorId"] as const)(
    "rejects a 129-character %s",
    async (field) => {
      const value = { ...mode, [field]: "a".repeat(129) };
      const fetcher = vi.fn().mockResolvedValue(Response.json(value));
      await expect(
        createRuntimeClient({ fetch: fetcher }).getMode(),
      ).rejects.toThrow();
    },
  );
  it.each([
    {},
    { ...mode, projectId: "" },
    { ...mode, mode: "personal" },
    { ...mode, identityMode: "personal" },
    { ...mode, role: "admin" },
  ])("rejects malformed runtime authority %j", async (value) => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(value));
    await expect(
      createRuntimeClient({ fetch: fetcher }).getMode(),
    ).rejects.toThrow();
  });
});
