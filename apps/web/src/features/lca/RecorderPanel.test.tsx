import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { it, expect, vi } from "vitest";
import { RecorderPanel } from "./RecorderPanel";
import {
  RecorderError,
  type RecorderClient,
  type Recording,
} from "./recorderClient";
import type { LcaDefinition } from "./client";
const definition: LcaDefinition = {
  schema_version: "lca-debug-v1",
  allowed_origins: ["http://example.test"],
  parameters: [],
  targets: [],
  steps: [
    {
      id: "entry",
      operation: "navigate",
      arguments: { url: "http://example.test" },
      timeout_ms: 1000,
    },
  ],
};
const recording: Recording = {
  session_id: "s1",
  flow_id: "f1",
  status: "recording",
  draft_version: 1,
};
function api(): RecorderClient {
  return {
    cancel: vi.fn(),
    list: vi.fn().mockResolvedValue([]),
    start: vi.fn().mockResolvedValue(recording),
    get: vi.fn().mockResolvedValue(recording),
    takeover: vi.fn(),
    grant: vi.fn().mockResolvedValue({
      capability: "opaque",
      control: { control_state: "human_active", control_epoch: 1 },
    }),
    frame: vi.fn().mockResolvedValue({
      control_epoch: 1,
      frame_sequence: 3,
      captured_at: new Date().toISOString(),
      viewport: { width: 800, height: 600 },
      media_type: "image/jpeg",
      image_base64: "YQ==",
    }),
    action: vi.fn().mockResolvedValue({ action_id: "a1", status: "completed" }),
    stop: vi.fn().mockResolvedValue({
      ...recording,
      status: "stopped",
      projection: {
        ready: true,
        definition,
        missing_sequences: [],
        excluded: [],
      },
    }),
  };
}
function setup(client = api()) {
  const onImport = vi.fn();
  render(
    <RecorderPanel
      projectId="p"
      flowId="f1"
      version={1}
      definition={definition}
      client={client}
      onImport={onImport}
    />,
  );
  return { client, onImport };
}
it("does not start on read; runs explicit recording then stops and imports without saving", async () => {
  const { client, onImport } = setup();
  const user = userEvent.setup();
  await screen.findByRole("button", { name: "Start recording" });
  expect(client.start).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Start recording" }));
  await screen.findByAltText("Live recording browser");
  fireEvent.change(screen.getByLabelText("Target CSS selector"), {
    target: { value: "#name" },
  });
  fireEvent.change(screen.getByLabelText("Recording operation"), {
    target: { value: "fill" },
  });
  fireEvent.change(screen.getByLabelText("Synthetic input value"), {
    target: { value: "Ada" },
  });
  await user.click(screen.getByRole("button", { name: "Perform action" }));
  expect(client.action).toHaveBeenCalledTimes(1);
  await user.click(
    screen.getByRole("button", { name: "Stop and drain recording" }),
  );
  expect(onImport).not.toHaveBeenCalled();
  await user.click(
    await screen.findByRole("button", { name: "Import recorded draft" }),
  );
  expect(onImport).toHaveBeenCalledWith(definition);
});
it("unknown action freezes writes and does not retry with a new identity", async () => {
  const client = api();
  client.action = vi.fn().mockRejectedValue(new TypeError("lost response"));
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await screen.findByAltText("Live recording browser");
  fireEvent.change(screen.getByLabelText("Target CSS selector"), {
    target: { value: "#submit" },
  });
  await user.click(screen.getByRole("button", { name: "Perform action" }));
  expect(await screen.findByText(/Action outcome unknown/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Perform action" })).toBeDisabled();
  expect(client.action).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("button", { name: "Stop and drain recording" }),
  ).toBeEnabled();
});
it("recovers existing recording explicitly without creating a second session", async () => {
  const client = api();
  client.list = vi.fn().mockResolvedValue([recording]);
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Open recording s1" }),
  );
  await screen.findByAltText("Live recording browser");
  expect(client.start).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "Start recording" }),
  ).not.toBeInTheDocument();
});
it("does not import while stop is draining or uncertain", async () => {
  const client = api();
  client.stop = vi.fn().mockResolvedValue({ ...recording, status: "draining" });
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await screen.findByAltText("Live recording browser");
  await user.click(
    screen.getByRole("button", { name: "Stop and drain recording" }),
  );
  await waitFor(() =>
    expect(screen.getByText("Recording state: draining")).toBeVisible(),
  );
  expect(
    screen.queryByRole("button", { name: "Import recorded draft" }),
  ).not.toBeInTheDocument();
});
it("guards import when editor changes after recording began", async () => {
  const client = api();
  const onImport = vi.fn();
  const props = {
    projectId: "p",
    flowId: "f1",
    version: 1,
    definition,
    client,
    onImport,
  };
  const view = render(<RecorderPanel {...props} />);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await screen.findByAltText("Live recording browser");
  view.rerender(
    <RecorderPanel
      {...props}
      definition={{
        ...definition,
        parameters: [{ name: "new", type: "string", required: false }],
      }}
    />,
  );
  await user.click(
    screen.getByRole("button", { name: "Stop and drain recording" }),
  );
  expect(
    await screen.findByRole("button", { name: "Import recorded draft" }),
  ).toBeDisabled();
  expect(onImport).not.toHaveBeenCalled();
});
it("fences rapid action clicks while an acknowledgement is pending", async () => {
  const client = api();
  let finish!: (receipt: { status: string }) => void;
  client.action = vi.fn().mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await screen.findByAltText("Live recording browser");
  fireEvent.change(screen.getByLabelText("Target CSS selector"), {
    target: { value: "#submit" },
  });
  const button = screen.getByRole("button", { name: "Perform action" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(client.action).toHaveBeenCalledTimes(1);
  finish({ status: "completed" });
  await screen.findByAltText("Live recording browser");
});
it("notifies the workspace about unresolved recordings found after reload", async () => {
  const client = api();
  client.list = vi.fn().mockResolvedValue([recording]);
  const onActiveChange = vi.fn();
  render(
    <RecorderPanel
      projectId="p"
      flowId="f1"
      version={1}
      definition={definition}
      client={client}
      onImport={vi.fn()}
      onActiveChange={onActiveChange}
    />,
  );
  await screen.findByRole("button", { name: "Open recording s1" });
  expect(onActiveChange).toHaveBeenLastCalledWith(true);
});
it("returns to preserved history after stop and permits a new session explicitly", async () => {
  const client = api();
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await screen.findByAltText("Live recording browser");
  await user.click(
    screen.getByRole("button", { name: "Stop and drain recording" }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Back to recording history" }),
  );
  expect(screen.getByRole("button", { name: "Start recording" })).toBeEnabled();
  expect(
    screen.getByRole("button", { name: "Open recording s1" }),
  ).toBeVisible();
  expect(client.start).toHaveBeenCalledTimes(1);
});
it("cancels an unknown session explicitly without claiming it is stopped", async () => {
  const client = api();
  client.action = vi.fn().mockRejectedValue(new TypeError("unknown"));
  client.cancel = vi
    .fn()
    .mockResolvedValue({ ...recording, status: "draining" });
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await screen.findByAltText("Live recording browser");
  fireEvent.change(screen.getByLabelText("Target CSS selector"), {
    target: { value: "#submit" },
  });
  await user.click(screen.getByRole("button", { name: "Perform action" }));
  await user.click(
    screen.getByRole("button", { name: "Cancel recording session" }),
  );
  expect(client.cancel).toHaveBeenCalledTimes(1);
  expect(await screen.findByText("Recording state: draining")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Import recorded draft" }),
  ).not.toBeInTheDocument();
});
it("ignores a delayed viewing frame captured before an action invalidated it", async () => {
  const client = api();
  const frame = {
    control_epoch: 1,
    frame_sequence: 3,
    captured_at: new Date().toISOString(),
    viewport: { width: 800, height: 600 },
    media_type: "image/jpeg" as const,
    image_base64: "YQ==",
  };
  let resolveOld!: (value: typeof frame) => void;
  client.frame = vi
    .fn()
    .mockResolvedValueOnce(frame)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValue({ ...frame, frame_sequence: 5, image_base64: "Yg==" });
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await screen.findByAltText("Live recording browser");
  await waitFor(() => expect(client.frame).toHaveBeenCalledTimes(2), {
    timeout: 2500,
  });
  fireEvent.change(screen.getByLabelText("Target CSS selector"), {
    target: { value: "#submit" },
  });
  await user.click(screen.getByRole("button", { name: "Perform action" }));
  await waitFor(() =>
    expect(screen.getByAltText("Live recording browser")).toHaveAttribute(
      "src",
      "data:image/jpeg;base64,Yg==",
    ),
  );
  resolveOld({ ...frame, frame_sequence: 4, image_base64: "Yw==" });
  await waitFor(() =>
    expect(screen.getByAltText("Live recording browser")).toHaveAttribute(
      "src",
      "data:image/jpeg;base64,Yg==",
    ),
  );
});
it("can retry initial recording history read without dispatching", async () => {
  const client = api();
  client.list = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockResolvedValue([]);
  setup(client);
  const user = userEvent.setup();
  await screen.findByRole("alert");
  await user.click(
    screen.getByRole("button", { name: "Refresh recording history" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Start recording" }),
    ).toBeEnabled(),
  );
  expect(client.start).not.toHaveBeenCalled();
});
it("releases a confirmed canceled lifecycle without importing unknown outcomes", async () => {
  const client = api();
  client.cancel = vi.fn().mockResolvedValue({
    ...recording,
    status: "canceled",
    outcome_unknown: true,
    projection: {
      ready: true,
      definition,
      missing_sequences: [],
      excluded: [{ action_id: "a1", sequence: 1, reason: "unknown" }],
    },
  });
  const { onImport } = setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await screen.findByAltText("Live recording browser");
  await user.click(
    screen.getByRole("button", { name: "Cancel recording session" }),
  );
  expect(
    await screen.findByText(/Some action outcomes remain unknown/),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Import recorded draft" }),
  ).not.toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "Back to recording history" }),
  );
  expect(screen.getByRole("button", { name: "Start recording" })).toBeEnabled();
  expect(onImport).not.toHaveBeenCalled();
});
it("preserves takeover authority while pending and reconciles only by reading", async () => {
  const client = api();
  client.grant = vi.fn().mockResolvedValue({
    capability: "original",
    controllerId: "controller-one",
    control: { control_state: "agent_active", control_epoch: 0 },
    expiresInSeconds: 300,
  });
  client.takeover = vi
    .fn()
    .mockResolvedValue({ control_state: "takeover_pending", control_epoch: 0 });
  client.get = vi
    .fn()
    .mockResolvedValueOnce({
      ...recording,
      status: "starting",
      control: { control_state: "takeover_pending", control_epoch: 0 },
    })
    .mockResolvedValue({
      ...recording,
      control: {
        control_state: "human_active",
        control_epoch: 1,
        controller_id: "controller-one",
      },
    });
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Take recording control" }),
  );
  expect(client.grant).toHaveBeenCalledTimes(1);
  expect(
    screen.queryByRole("button", { name: "Perform action" }),
  ).not.toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "Refresh recording status" }),
  );
  await screen.findByAltText("Live recording browser");
  expect(client.grant).toHaveBeenCalledTimes(1);
  expect(client.takeover).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("Target CSS selector"), {
    target: { value: "#submit" },
  });
  await user.click(screen.getByRole("button", { name: "Perform action" }));
  expect(vi.mocked(client.action).mock.calls[0][5]).toBe("original");
});
it("does not send another takeover after a lost admission response", async () => {
  const client = api();
  client.grant = vi.fn().mockResolvedValue({
    capability: "original",
    controllerId: "controller-one",
    control: { control_state: "agent_active", control_epoch: 0 },
  });
  client.takeover = vi.fn().mockRejectedValue(new TypeError("lost admission"));
  client.get = vi.fn().mockResolvedValue({
    ...recording,
    control: {
      control_state: "human_active",
      control_epoch: 1,
      controller_id: "controller-one",
    },
  });
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Take recording control" }),
  );
  await user.click(
    screen.getByRole("button", { name: "Refresh recording status" }),
  );
  await screen.findByAltText("Live recording browser");
  expect(client.takeover).toHaveBeenCalledTimes(1);
  expect(client.grant).toHaveBeenCalledTimes(1);
});
it("clears the local unknown warning once drain confirms a terminal status", async () => {
  const client = api();
  client.stop = vi.fn().mockResolvedValue({ ...recording, status: "draining" });
  client.get = vi.fn().mockResolvedValue({ ...recording, status: "stopped" });
  setup(client);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Start recording" }),
  );
  await screen.findByAltText("Live recording browser");
  await user.click(
    screen.getByRole("button", { name: "Stop and drain recording" }),
  );
  expect(screen.getByText(/Further writes are blocked/)).toBeVisible();
  await user.click(
    screen.getByRole("button", { name: "Refresh recording status" }),
  );
  expect(await screen.findByText("Recording state: stopped")).toBeVisible();
  expect(
    screen.queryByText(/Further writes are blocked/),
  ).not.toBeInTheDocument();
});
it("does not leave an unconfirmed warning when the final bounded poll confirms control", async () => {
  vi.useFakeTimers();
  try {
    const client = api();
    client.grant = vi.fn().mockResolvedValue({
      capability: "original",
      controllerId: "c1",
      control: { control_state: "agent_active", control_epoch: 0 },
      expiresInSeconds: 300,
    });
    client.takeover = vi.fn().mockResolvedValue({
      control_state: "takeover_pending",
      control_epoch: 0,
    });
    let reads = 0;
    client.get = vi.fn().mockImplementation(async () =>
      ++reads >= 31
        ? {
            ...recording,
            control: {
              control_state: "human_active",
              control_epoch: 1,
              controller_id: "c1",
            },
          }
        : {
            ...recording,
            status: "starting",
            control: { control_state: "takeover_pending", control_epoch: 0 },
          },
    );
    setup(client);
    await act(async () => {});
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start recording" }));
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Take recording control" }),
      );
    });
    for (let i = 0; i < 30; i++)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    expect(client.get).toHaveBeenCalledTimes(31);
    expect(screen.getByAltText("Live recording browser")).toBeVisible();
    expect(
      screen.queryByText(/Takeover remains unconfirmed/),
    ).not.toBeInTheDocument();
    expect(client.takeover).toHaveBeenCalledTimes(1);
  } finally {
    vi.useRealTimers();
  }
});

function pendingFrame() {
  return Object.assign(new RecorderError(409), { code: "live_frame_pending" });
}
function freshFrame(sequence = 10) {
  return {
    control_epoch: 1,
    frame_sequence: sequence,
    captured_at: new Date().toISOString(),
    viewport: { width: 800, height: 600 },
    media_type: "image/jpeg" as const,
    image_base64: "Yg==",
  };
}
async function clickButton(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}
async function advanceFrames(count = 1) {
  for (let i = 0; i < count; i++)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
}
it.each(["takeover", "background", "completed action"])(
  "recovers pending frames after %s without repeating writes",
  async (stage) => {
    vi.useFakeTimers();
    try {
      const client = api();
      const frames = vi
        .fn()
        .mockRejectedValueOnce(pendingFrame())
        .mockRejectedValueOnce(pendingFrame())
        .mockImplementation(async () => freshFrame());
      if (stage !== "takeover")
        frames
          .mockReset()
          .mockImplementationOnce(async () => freshFrame(3))
          .mockRejectedValueOnce(pendingFrame())
          .mockRejectedValueOnce(pendingFrame())
          .mockImplementation(async () => freshFrame());
      client.frame = frames;
      if (stage === "takeover") {
        client.grant = vi.fn().mockResolvedValue({
          capability: "opaque",
          control: { control_state: "agent_active", control_epoch: 0 },
        });
        client.takeover = vi.fn().mockResolvedValue({
          control_state: "takeover_pending",
          control_epoch: 0,
        });
        client.get = vi.fn().mockResolvedValue({
          ...recording,
          control: { control_state: "human_active", control_epoch: 1 },
        });
      }
      setup(client);
      await act(async () => {});
      await clickButton("Start recording");
      if (stage === "takeover") await clickButton("Take recording control");
      fireEvent.change(screen.getByLabelText("Target CSS selector"), {
        target: { value: "#name" },
      });
      if (stage === "completed action") {
        await clickButton("Perform action");
        expect(screen.getByText(/click: completed/)).toBeVisible();
      } else if (stage === "background") await advanceFrames();
      expect(
        screen.getByRole("button", { name: "Perform action" }),
      ).toBeDisabled();
      expect(
        screen.queryByText(/Further writes are blocked/),
      ).not.toBeInTheDocument();
      await advanceFrames(2);
      expect(screen.getByAltText("Live recording browser")).toBeVisible();
      expect(
        screen.getByRole("button", { name: "Perform action" }),
      ).toBeEnabled();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(client.start).toHaveBeenCalledTimes(1);
      expect(client.takeover).toHaveBeenCalledTimes(
        stage === "takeover" ? 1 : 0,
      );
      expect(client.action).toHaveBeenCalledTimes(
        stage === "completed action" ? 1 : 0,
      );
    } finally {
      vi.useRealTimers();
    }
  },
);
it("bounds pending frame reads and permits only an explicit fresh read window afterward", async () => {
  vi.useFakeTimers();
  try {
    const client = api();
    client.frame = vi.fn().mockRejectedValue(pendingFrame());
    setup(client);
    await act(async () => {});
    await clickButton("Start recording");
    await advanceFrames(10);
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Live frame.*15 seconds/,
    );
    const reads = vi.mocked(client.frame).mock.calls.length;
    await advanceFrames(20);
    expect(client.frame).toHaveBeenCalledTimes(reads);
    expect(
      screen.getByRole("button", { name: "Perform action" }),
    ).toBeDisabled();
    vi.mocked(client.frame)
      .mockRejectedValueOnce(pendingFrame())
      .mockImplementation(async () => freshFrame());
    await clickButton("Refresh live frame");
    await advanceFrames();
    expect(screen.getByAltText("Live recording browser")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(client.grant).toHaveBeenCalledTimes(1);
    expect(client.action).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});
it.each(["expiry", "cancel", "unmount"])(
  "fences pending frame retries on %s without renewing access",
  async (ending) => {
    vi.useFakeTimers();
    try {
      const client = api();
      client.frame = vi.fn().mockRejectedValue(pendingFrame());
      client.grant = vi.fn().mockResolvedValue({
        capability: "opaque",
        expiresInSeconds: 2,
        control: { control_state: "human_active", control_epoch: 1 },
      });
      client.cancel = vi
        .fn()
        .mockResolvedValue({ ...recording, status: "canceled" });
      const view = render(
        <RecorderPanel
          projectId="p"
          flowId="f1"
          version={1}
          definition={definition}
          client={client}
          onImport={vi.fn()}
        />,
      );
      await act(async () => {});
      await clickButton("Start recording");
      if (ending === "cancel") await clickButton("Cancel recording session");
      if (ending === "unmount") view.unmount();
      await advanceFrames(5);
      expect(client.frame).toHaveBeenCalledTimes(ending === "expiry" ? 2 : 1);
      expect(client.grant).toHaveBeenCalledTimes(1);
      expect(client.action).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  },
);
it.each([403, 409])(
  "does not automatically retry a background frame error without a trusted pending code (%s)",
  async (status) => {
    vi.useFakeTimers();
    try {
      const client = api();
      client.frame = vi
        .fn()
        .mockImplementationOnce(async () => freshFrame())
        .mockRejectedValue(new RecorderError(status));
      setup(client);
      await act(async () => {});
      await clickButton("Start recording");
      await advanceFrames(5);
      expect(client.frame).toHaveBeenCalledTimes(2);
      expect(screen.getByRole("alert")).toHaveTextContent(
        /Live updates paused/,
      );
      expect(
        screen.getByRole("button", { name: "Perform action" }),
      ).toBeDisabled();
      expect(client.action).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  },
);
it("ignores a delayed pending frame after an action becomes unknown", async () => {
  vi.useFakeTimers();
  try {
    const client = api();
    let rejectOld!: (error: Error) => void;
    client.frame = vi
      .fn()
      .mockImplementationOnce(async () => freshFrame())
      .mockImplementationOnce(
        () =>
          new Promise((_, reject) => {
            rejectOld = reject;
          }),
      )
      .mockImplementation(async () => freshFrame());
    client.action = vi
      .fn()
      .mockRejectedValue(new TypeError("lost action response"));
    setup(client);
    await act(async () => {});
    await clickButton("Start recording");
    fireEvent.change(screen.getByLabelText("Target CSS selector"), {
      target: { value: "#submit" },
    });
    await advanceFrames();
    await clickButton("Perform action");
    await act(async () => {
      rejectOld(pendingFrame());
    });
    await advanceFrames(5);
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Action outcome unknown/,
    );
    expect(screen.getByText(/Further writes are blocked/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Perform action" }),
    ).toBeDisabled();
    expect(client.action).toHaveBeenCalledTimes(1);
    expect(client.frame).toHaveBeenCalledTimes(2);
  } finally {
    vi.useRealTimers();
  }
});
it.each(["stale", "wrong epoch"])(
  "does not make a %s frame writable after pending recovery",
  async (kind) => {
    vi.useFakeTimers();
    try {
      const client = api();
      client.frame = vi
        .fn()
        .mockRejectedValueOnce(pendingFrame())
        .mockImplementation(async () => ({
          ...freshFrame(),
          ...(kind === "stale"
            ? { captured_at: new Date(Date.now() - 5000).toISOString() }
            : { control_epoch: 2 }),
        }));
      setup(client);
      await act(async () => {});
      await clickButton("Start recording");
      fireEvent.change(screen.getByLabelText("Target CSS selector"), {
        target: { value: "#submit" },
      });
      await advanceFrames();
      expect(screen.getByAltText("Live recording browser")).toBeVisible();
      expect(
        screen.getByRole("button", { name: "Perform action" }),
      ).toBeDisabled();
      expect(client.action).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  },
);
it("times out pending observation even when the next read hangs and ignores its late success", async () => {
  vi.useFakeTimers();
  try {
    const client = api();
    let finish!: (frame: ReturnType<typeof freshFrame>) => void;
    client.frame = vi
      .fn()
      .mockRejectedValueOnce(pendingFrame())
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
    setup(client);
    await act(async () => {});
    await clickButton("Start recording");
    await advanceFrames(10);
    expect(screen.getByRole("alert")).toHaveTextContent(
      /Live frame.*15 seconds/,
    );
    await act(async () => {
      finish(freshFrame());
    });
    await advanceFrames(3);
    expect(
      screen.queryByAltText("Live recording browser"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Perform action" }),
    ).toBeDisabled();
    expect(client.frame).toHaveBeenCalledTimes(2);
    expect(client.action).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});
it("does not let an older pending read erase a manually refreshed frame", async () => {
  vi.useFakeTimers();
  try {
    const client = api();
    let rejectOld!: (error: Error) => void;
    client.frame = vi
      .fn()
      .mockImplementationOnce(async () => freshFrame(3))
      .mockImplementationOnce(
        () =>
          new Promise((_, reject) => {
            rejectOld = reject;
          }),
      )
      .mockImplementation(async () => freshFrame(10));
    setup(client);
    await act(async () => {});
    await clickButton("Start recording");
    await advanceFrames();
    await clickButton("Refresh live frame");
    await act(async () => {
      rejectOld(pendingFrame());
    });
    expect(screen.getByAltText("Live recording browser")).toBeVisible();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(client.frame).toHaveBeenCalledTimes(3);
  } finally {
    vi.useRealTimers();
  }
});
