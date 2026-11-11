import { afterEach, expect, it } from "vitest";
import { BrowserRecovery } from "./browserRecovery";
const context = {
  available: true as const,
  project_id: "project-1",
  actor_id: "actor-1",
  identity_mode: "validation" as const,
  gateway_instance_id: "gateway-1",
  context_binding: "a".repeat(64),
};
const request = {
  message: "Read order",
  modelAlias: "model-1",
  browser: { startUrl: "http://example.test", expectedResult: "Order visible" },
};
afterEach(() => sessionStorage.clear());
it("retains exactly one frozen browser request and key across reload, refusing a different intent", () => {
  const first = new BrowserRecovery(sessionStorage, context);
  const pending = first.prepare("draft", request);
  const reloaded = new BrowserRecovery(sessionStorage, context);
  expect(reloaded.pending()).toEqual(pending);
  expect(reloaded.prepare("draft", request).key).toBe(pending.key);
  expect(() =>
    reloaded.prepare("draft", { ...request, message: "Changed" }),
  ).toThrow(/unconfirmed/i);
  expect(reloaded.pending()?.input.message).toBe("Read order");
});
it("does not reuse a browser request under another authorized gateway or actor", () => {
  new BrowserRecovery(sessionStorage, context).prepare("draft", request);
  expect(() =>
    new BrowserRecovery(sessionStorage, {
      ...context,
      actor_id: "other",
    }).pending(),
  ).toThrow(/context/i);
});
it("blocks execution when session recovery is unavailable, corrupt, or contains unapproved fields", () => {
  const storage = {
    getItem: () => {
      throw new Error("denied");
    },
    setItem: () => undefined,
    removeItem: () => undefined,
  };
  expect(() =>
    new BrowserRecovery(storage, context).prepare("draft", request),
  ).toThrow(/recovery/i);
  expect(() =>
    new BrowserRecovery(sessionStorage, context).prepare("draft", {
      ...request,
      authorization: "secret",
    } as typeof request),
  ).toThrow(/recovery/i);
  sessionStorage.setItem("tap:browser-intent:v1", "not json");
  expect(() => new BrowserRecovery(sessionStorage, context).pending()).toThrow(
    /recovery/i,
  );
});
it("retains accepted conversation reference without persisting headers or credentials", () => {
  const recovery = new BrowserRecovery(sessionStorage, context);
  const pending = recovery.prepare("draft", request);
  recovery.accept(pending.key, "conversation-1");
  const reloaded = new BrowserRecovery(sessionStorage, context);
  expect(reloaded.pending()).toBeNull();
  expect(reloaded.conversation()).toBe("conversation-1");
  expect(sessionStorage.getItem("tap:browser-intent:v1")).not.toContain(
    "Read order",
  );
});

it("releases a first definite pre-admission rejection but never releases a prior unknown POST on a later rejection", () => {
  const recovery = new BrowserRecovery(sessionStorage, context);
  const rejected = recovery.prepare("draft", request);
  recovery.markAttempt(rejected.key);
  expect(recovery.rejectUnadmitted(rejected.key, 422)).toBe(true);
  expect(recovery.pending()).toBeNull();
  const unknown = recovery.prepare("draft", request);
  recovery.markAttempt(unknown.key);
  expect(recovery.rejectUnadmitted(unknown.key, 503)).toBe(false);
  recovery.markAttempt(unknown.key);
  expect(recovery.rejectUnadmitted(unknown.key, 403)).toBe(false);
  expect(recovery.pending()?.key).toBe(unknown.key);
});
it("allows a new intent after an accepted task including one later canceled", () => {
  const recovery = new BrowserRecovery(sessionStorage, context);
  const first = recovery.prepare("draft", request);
  recovery.accept(first.key, "conversation-1");
  const next = recovery.prepare("conversation-1", {
    ...request,
    message: "Next run",
  });
  expect(next.key).not.toBe(first.key);
});

it("exposes only a known read-only conversation after a gateway restart and permits rebinding only a confirmed receipt", () => {
  const first = new BrowserRecovery(sessionStorage, context);
  const pending = first.prepare("draft", request);
  const restarted = new BrowserRecovery(sessionStorage, {
    ...context,
    gateway_instance_id: "restarted",
    context_binding: "b".repeat(64),
  });
  expect(restarted.reference()).toEqual({
    conversationId: null,
    unconfirmed: true,
  });
  expect(() => restarted.adoptAccepted("conversation-1")).toThrow(
    /unconfirmed/i,
  );
  first.accept(pending.key, "conversation-1");
  expect(restarted.reference()).toEqual({
    conversationId: "conversation-1",
    unconfirmed: false,
  });
  restarted.adoptAccepted("conversation-1");
  expect(restarted.conversation()).toBe("conversation-1");
});
