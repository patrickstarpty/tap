import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LcaAssistantPanel } from "./LcaAssistantPanel";

const context = {
  project_id: "project1",
  actor_id: "actor1",
  identity_mode: "validation" as const,
  gateway_instance_id: "gateway-1",
  context_binding: "a".repeat(64),
};
const source = {
  task_id: "task1",
  execution_id: "execution1",
  bundle_id: "bundle1",
  input_sha256: "b".repeat(64),
};
afterEach(() => vi.unstubAllEnvs());
function setup() {
  vi.stubEnv("VITE_TAPPER_URL", "http://127.0.0.1:5173");
  const onSourceChange = vi.fn();
  const view = render(
    <LcaAssistantPanel context={context} onSourceChange={onSourceChange} />,
  );
  const frame = screen.getByTitle("Tapper LCA assistant") as HTMLIFrameElement;
  const post = vi.spyOn(frame.contentWindow!, "postMessage");
  fireEvent.load(frame);
  const init = post.mock.calls[0][0];
  const send = (
    data: unknown,
    origin = "http://127.0.0.1:5173",
    sender: MessageEventSource | null = frame.contentWindow,
  ) => {
    act(() =>
      window.dispatchEvent(
        new MessageEvent("message", { data, origin, source: sender }),
      ),
    );
  };
  const ready = { ...init, type: "lca:ready" };
  const review = {
    ...init,
    type: "lca:review",
    request_id: "request1",
    source,
  };
  onSourceChange.mockClear();
  return { ...view, frame, post, init, ready, review, send, onSourceChange };
}
it("pins the real iframe origin and keeps the frame alive while hidden", () => {
  const { frame, post, init, send, ready } = setup();
  expect(frame.src).toBe("http://127.0.0.1:5173/embed/lca");
  expect(frame).toHaveAttribute(
    "sandbox",
    "allow-scripts allow-same-origin allow-popups allow-downloads",
  );
  expect(post).toHaveBeenCalledWith(
    {
      type: "lca:init",
      schema_version: "lca-assistant-v1",
      nonce: expect.any(String),
      context,
    },
    "http://127.0.0.1:5173",
  );
  expect(init.nonce.length).toBeGreaterThanOrEqual(32);
  send(ready);
  expect(screen.getByText("Tapper connected")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Hide Tapper" }));
  expect(frame).toBeInTheDocument();
  expect(frame).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Open Tapper" }));
  expect(screen.getByTitle("Tapper LCA assistant")).toBe(frame);
  expect(post).toHaveBeenCalledTimes(1);
});
it("only forwards a user review source after the bound iframe handshake and deduplicates it", () => {
  const { send, review, ready, onSourceChange } = setup();
  send(review);
  expect(onSourceChange).not.toHaveBeenCalled();
  send(ready);
  send(review);
  send(review);
  expect(onSourceChange).toHaveBeenCalledExactlyOnceWith(source);
});
it.each([
  ["origin", { origin: "https://forged.example" }],
  ["sender", { sender: window }],
  ["nonce", { nonce: "old-nonce" }],
  ["schema", { schema_version: "v0" }],
  ["project", { context: { ...context, project_id: "other" } }],
  ["actor", { context: { ...context, actor_id: "other" } }],
  ["gateway", { context: { ...context, gateway_instance_id: "other" } }],
  ["binding", { context: { ...context, context_binding: "c".repeat(64) } }],
  ["mode", { context: { ...context, identity_mode: "production" } }],
  ["oversized ID", { source: { ...source, task_id: "a".repeat(81) } }],
  ["source path", { source: { ...source, task_id: "../other" } }],
  ["digest", { source: { ...source, input_sha256: "invalid" } }],
  ["draft payload", { definition: { steps: [] } }],
  ["source payload", { source: { ...source, definition: { steps: [] } } }],
] as const)("rejects a forged %s", (_name, overrides) => {
  const { send, ready, review, onSourceChange, frame } = setup();
  send(ready);
  const { origin, sender, ...data } = overrides as Record<string, unknown>;
  send(
    { ...review, ...data },
    origin as string | undefined,
    sender === undefined ? frame.contentWindow : (sender as Window),
  );
  expect(onSourceChange).not.toHaveBeenCalled();
});
it("rejects stale messages after frame reload and requires a new handshake", () => {
  const { send, ready, review, frame, post, onSourceChange } = setup();
  send(ready);
  fireEvent.load(frame);
  const next = post.mock.calls[1][0];
  expect(next.nonce).not.toBe(ready.nonce);
  onSourceChange.mockClear();
  send(ready);
  send(review);
  expect(onSourceChange).not.toHaveBeenCalled();
  send({ ...next, type: "lca:ready" });
  send({ ...review, nonce: next.nonce });
  expect(onSourceChange).toHaveBeenCalledExactlyOnceWith(source);
});
it("invalidates ready state and pending source if the iframe disconnects", () => {
  const { send, ready, review, frame, onSourceChange } = setup();
  send(ready);
  fireEvent.error(frame);
  expect(screen.getByRole("alert")).toHaveTextContent("unavailable");
  onSourceChange.mockClear();
  send(review);
  expect(onSourceChange).not.toHaveBeenCalled();
});
it("cannot accept a message from a detached frame", () => {
  const { send, ready, review, frame, onSourceChange } = setup();
  send(ready);
  const holder = frame.parentNode!;
  holder.removeChild(frame);
  send(review);
  expect(onSourceChange).not.toHaveBeenCalled();
  holder.appendChild(frame);
});
it("rebinds context without accepting messages from the previous project", () => {
  const { rerender, frame, post, send, ready, review, onSourceChange } =
    setup();
  send(ready);
  rerender(
    <LcaAssistantPanel
      context={{ ...context, project_id: "project2" }}
      onSourceChange={onSourceChange}
    />,
  );
  expect(post).toHaveBeenCalledTimes(1);
  const nextPost = vi.spyOn(frame.contentWindow!, "postMessage");
  fireEvent.load(frame);
  expect(nextPost.mock.calls.at(-1)?.[0].context.project_id).toBe("project2");
  onSourceChange.mockClear();
  send(review);
  expect(onSourceChange).not.toHaveBeenCalled();
});
it("fails closed for an invalid configured Tapper URL", () => {
  vi.stubEnv("VITE_TAPPER_URL", "javascript:alert(1)");
  render(<LcaAssistantPanel context={context} onSourceChange={vi.fn()} />);
  expect(screen.queryByTitle("Tapper LCA assistant")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("configuration");
});
it("reports a missing handshake and explicitly reconnects without remounting the iframe", () => {
  vi.useFakeTimers();
  try {
    const { frame, send, ready, review, onSourceChange } = setup();
    act(() => vi.advanceTimersByTime(10000));
    expect(screen.getByRole("alert")).toHaveTextContent("unavailable");
    onSourceChange.mockClear();
    send(ready);
    send(review);
    expect(onSourceChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Reconnect Tapper" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTitle("Tapper LCA assistant")).toBe(frame);
    const post = vi.spyOn(frame.contentWindow!, "postMessage");
    fireEvent.load(frame);
    const init = post.mock.calls.at(-1)![0];
    send(
      { ...init, type: "lca:ready" },
      "http://127.0.0.1:5173",
      frame.contentWindow,
    );
    expect(screen.getByText("Tapper connected")).toBeVisible();
  } finally {
    vi.useRealTimers();
  }
});

it("does not rotate a verified nonce on cloned context or callback rerenders", () => {
  const { rerender, frame, post, send, ready, review, onSourceChange } =
    setup();
  send(ready);
  const nextCallback = vi.fn();
  rerender(
    <LcaAssistantPanel
      context={{ ...context }}
      onSourceChange={nextCallback}
    />,
  );
  expect(post).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Tapper connected")).toBeVisible();
  send(review, "http://127.0.0.1:5173", frame.contentWindow);
  expect(nextCallback).toHaveBeenCalledExactlyOnceWith(source);
  expect(onSourceChange).not.toHaveBeenCalled();
});
