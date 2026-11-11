import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LcaApplication } from "./LcaApplication";

const context = {
  available: true,
  project_id: "verified-project",
  actor_id: "actor1",
  identity_mode: "validation",
  gateway_instance_id: "gateway-1",
  context_binding: "a".repeat(64),
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("uses verified server project context without a demo fallback", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      calls.push(path);
      return new Response(
        JSON.stringify(path === "/api/lca/context" ? context : []),
        { status: 200 },
      );
    }),
  );
  render(<LcaApplication />);
  expect(
    await screen.findByRole("heading", { name: "Low Code Automation" }),
  ).toBeVisible();
  await waitFor(() =>
    expect(calls).toContain("/api/projects/verified-project/lca/flows"),
  );
});
it("does not launch real workspace when authorized gateway is unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({ project_id: "verified-project", available: false }),
          { status: 200 },
        ),
    ),
  );
  render(<LcaApplication />);
  expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
  expect(
    screen.queryByRole("heading", { name: "Low Code Automation" }),
  ).not.toBeInTheDocument();
});

it.each([
  { ...context, actor_id: undefined },
  { ...context, context_binding: undefined },
  { ...context, gateway_instance_id: "" },
  { ...context, identity_mode: "production" },
  { ...context, project_id: "../other" },
])(
  "fails closed for incomplete or unsupported assistant context %j",
  async (value) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (path: string) =>
          new Response(
            JSON.stringify(path === "/api/lca/context" ? value : []),
          ),
      ),
    );
    render(<LcaApplication />);
    expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
    expect(screen.queryByTitle("Tapper LCA assistant")).not.toBeInTheDocument();
  },
);
it("keeps unsaved edits and the real assistant mounted across hide and reopen", async () => {
  const fetcher = vi.fn(
    async (path: string) =>
      new Response(JSON.stringify(path === "/api/lca/context" ? context : [])),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<LcaApplication />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "New debug flow" }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "New debug flow" }));
  fireEvent.change(screen.getByLabelText("Flow title"), {
    target: { value: "My unsaved flow" },
  });
  const frame = screen.getByTitle("Tapper LCA assistant");
  fireEvent.click(screen.getByRole("button", { name: "Hide Tapper" }));
  expect(frame).not.toBeVisible();
  expect(screen.getByLabelText("Flow title")).toHaveValue("My unsaved flow");
  fireEvent.click(screen.getByRole("button", { name: "Open Tapper" }));
  expect(screen.getByTitle("Tapper LCA assistant")).toBe(frame);
  expect(screen.getByLabelText("Flow title")).toHaveValue("My unsaved flow");
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    "/api/lca/context",
    "/api/projects/verified-project/lca/flows",
  ]);
});
it("routes a verified child source to explicit authoritative preview and unsaved import only", async () => {
  const source = {
    task_id: "task1",
    execution_id: "run1",
    bundle_id: "bundle1",
    input_sha256: "b".repeat(64),
  };
  const definition = {
    schema_version: "lca-debug-v1",
    allowed_origins: ["https://example.com"],
    targets: [],
    parameters: [],
    steps: [],
  };
  const fetcher = vi.fn(
    async (path: string) =>
      new Response(
        JSON.stringify(
          path === "/api/lca/context"
            ? context
            : path.endsWith("/preview")
              ? {
                  schema_version: "aa-lca-draft-v1",
                  complete: true,
                  issues: [],
                  source,
                  definition,
                }
              : [],
        ),
      ),
  );
  vi.stubGlobal("fetch", fetcher);
  render(<LcaApplication />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "New debug flow" }),
    ).toBeEnabled(),
  );
  const frame = screen.getByTitle("Tapper LCA assistant") as HTMLIFrameElement;
  const post = vi.spyOn(frame.contentWindow!, "postMessage");
  fireEvent.load(frame);
  const init = post.mock.calls[0][0];
  const send = (data: unknown) =>
    act(() =>
      window.dispatchEvent(
        new MessageEvent("message", {
          data,
          source: frame.contentWindow,
          origin: "http://127.0.0.1:5173",
        }),
      ),
    );
  send({ ...init, type: "lca:ready" });
  send({ ...init, type: "lca:review", request_id: "request1", source });
  expect(screen.getByLabelText("AA task ID")).toHaveValue("task1");
  expect(screen.queryByLabelText("Flow title")).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Preview AA task" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Use as unsaved draft" }),
  );
  expect(screen.getByLabelText("Flow title")).toHaveValue("AA task task1");
  expect(
    screen.getByText(/AA actions loaded as an unsaved draft/),
  ).toBeVisible();
  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    "/api/lca/context",
    "/api/projects/verified-project/lca/flows",
    "/api/projects/verified-project/lca/agent-tasks/task1/preview",
  ]);
});

it.each(["new gateway", "same gateway"])(
  "refreshes parent authority on reconnect with %s without losing the LCA draft",
  async (restart) => {
    let contextReads = 0;
    const next =
      restart === "new gateway"
        ? {
            ...context,
            gateway_instance_id: "gateway-2",
            context_binding: "c".repeat(64),
          }
        : context;
    const fetcher = vi.fn(
      async (path: string) =>
        new Response(
          JSON.stringify(
            path === "/api/lca/context"
              ? ++contextReads === 1
                ? context
                : next
              : [],
          ),
        ),
    );
    vi.stubGlobal("fetch", fetcher);
    render(<LcaApplication />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "New debug flow" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "New debug flow" }));
    fireEvent.change(screen.getByLabelText("Flow title"), {
      target: { value: "Preserve on restart" },
    });
    const editor = screen.getByLabelText("Flow title");
    const frame = screen.getByTitle(
      "Tapper LCA assistant",
    ) as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, "postMessage");
    fireEvent.load(frame);
    const firstInit = post.mock.calls[0][0];
    act(() =>
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { ...firstInit, type: "lca:ready" },
          source: frame.contentWindow,
          origin: "http://127.0.0.1:5173",
        }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Reconnect Tapper" }));
    await waitFor(() => expect(contextReads).toBe(2));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Reconnect Tapper" }),
      ).toBeEnabled(),
    );
    const nextPost = vi.spyOn(frame.contentWindow!, "postMessage");
    fireEvent.load(frame);
    const init = nextPost.mock.calls.at(-1)![0];
    expect(init.context.gateway_instance_id).toBe(next.gateway_instance_id);
    expect(init.context.context_binding).toBe(next.context_binding);
    expect(init.nonce).not.toBe(firstInit.nonce);
    act(() =>
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { ...init, type: "lca:ready" },
          source: frame.contentWindow,
          origin: "http://127.0.0.1:5173",
        }),
      ),
    );
    expect(screen.getByText("Tapper connected")).toBeVisible();
    expect(screen.getByTitle("Tapper LCA assistant")).toBe(frame);
    expect(screen.getByLabelText("Flow title")).toBe(editor);
    expect(editor).toHaveValue("Preserve on restart");
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      "/api/lca/context",
      "/api/projects/verified-project/lca/flows",
      "/api/lca/context",
    ]);
  },
);
it.each(["project_id", "actor_id", "identity_mode"])(
  "preserves the draft but blocks authority changes to %s until original scope is restored",
  async (field) => {
    let contextReads = 0;
    const fetcher = vi.fn(
      async (path: string) =>
        new Response(
          JSON.stringify(
            path === "/api/lca/context"
              ? ++contextReads === 2
                ? { ...context, [field]: "changed" }
                : context
              : [],
          ),
        ),
    );
    vi.stubGlobal("fetch", fetcher);
    render(<LcaApplication />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "New debug flow" }),
      ).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole("button", { name: "New debug flow" }));
    fireEvent.change(screen.getByLabelText("Flow title"), {
      target: { value: "Keep original project draft" },
    });
    const editor = screen.getByLabelText("Flow title");
    const frame = screen.getByTitle(
      "Tapper LCA assistant",
    ) as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, "postMessage");
    fireEvent.load(frame);
    const firstInit = post.mock.calls[0][0];
    fireEvent.click(screen.getByRole("button", { name: "Reconnect Tapper" }));
    expect(
      await screen.findByText(
        /Restore the original authorized project and actor/,
      ),
    ).toBeVisible();
    expect(screen.getByLabelText("Flow title")).toBe(editor);
    expect(editor).toHaveValue("Keep original project draft");
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
    expect(frame).toHaveAttribute("inert");
    fireEvent.load(frame);
    expect(post).toHaveBeenCalledTimes(1);
    act(() =>
      window.dispatchEvent(
        new MessageEvent("message", {
          data: { ...firstInit, type: "lca:ready" },
          source: frame.contentWindow,
          origin: "http://127.0.0.1:5173",
        }),
      ),
    );
    expect(screen.queryByText("Tapper connected")).not.toBeInTheDocument();
    expect(screen.getByTitle("Tapper LCA assistant")).toBe(frame);
    fireEvent.click(screen.getByRole("button", { name: "Reconnect Tapper" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save draft" })).toBeEnabled(),
    );
    expect(editor).toHaveValue("Keep original project draft");
    expect(
      fetcher.mock.calls.filter(([path]) => path.endsWith("/flows")),
    ).toHaveLength(1);
  },
);
it("blocks repeated reconnects and stale iframe messages during a pending context refresh", async () => {
  let contextReads = 0;
  let finish!: (response: Response) => void;
  let signal!: AbortSignal;
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => {
    if (path === "/api/lca/context" && ++contextReads === 2) {
      signal = options!.signal as AbortSignal;
      return new Promise<Response>((resolve) => {
        finish = resolve;
      });
    }
    return new Response(
      JSON.stringify(path === "/api/lca/context" ? context : []),
    );
  });
  vi.stubGlobal("fetch", fetcher);
  const view = render(<LcaApplication />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "New debug flow" }),
    ).toBeEnabled(),
  );
  const frame = screen.getByTitle("Tapper LCA assistant") as HTMLIFrameElement;
  const post = vi.spyOn(frame.contentWindow!, "postMessage");
  fireEvent.load(frame);
  const init = post.mock.calls[0][0];
  fireEvent.click(screen.getByRole("button", { name: "Reconnect Tapper" }));
  expect(
    screen.getByRole("button", { name: "Reconnect Tapper" }),
  ).toBeDisabled();
  expect(screen.getByRole("button", { name: "New debug flow" })).toBeDisabled();
  expect(frame).toHaveAttribute("inert");
  fireEvent.click(screen.getByRole("button", { name: "Reconnect Tapper" }));
  expect(contextReads).toBe(2);
  fireEvent.load(frame);
  expect(post).toHaveBeenCalledTimes(1);
  act(() =>
    window.dispatchEvent(
      new MessageEvent("message", {
        data: { ...init, type: "lca:ready" },
        source: frame.contentWindow,
        origin: "http://127.0.0.1:5173",
      }),
    ),
  );
  expect(screen.queryByText("Tapper connected")).not.toBeInTheDocument();
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finish(new Response(JSON.stringify(context))));
  expect(fetcher).toHaveBeenCalledTimes(3);
});
