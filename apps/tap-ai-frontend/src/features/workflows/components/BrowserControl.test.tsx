import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import {
  BrowserControl,
  type BrowserControlTransport,
  type ControlSnapshot,
} from "./BrowserControl";

const active: ControlSnapshot = {
  control_state: "agent_active",
  control_epoch: 0,
  controller_kind: "agent",
  controller_id: "agent-one",
  deadline_at: new Date(Date.now() + 60000).toISOString(),
  allowed_controls: ["request_takeover"],
};
function fixture(initial = active) {
  let state = initial;
  const transport: BrowserControlTransport = {
    issue: vi.fn(async () => ({
      capability: "private-panel-token",
      controllerId: "panel-one",
      control: state,
    })),
    state: vi.fn(async () => state),
    control: vi.fn(async (_capability, command) => {
      state = {
        ...state,
        control_state:
          command.operation === "takeover"
            ? "takeover_pending"
            : "handback_pending",
        allowed_controls: [],
      };
      return state;
    }),
  };
  return {
    transport,
    setState: (next: ControlSnapshot) => {
      state = next;
    },
  };
}
it("waits for confirmed ownership, then waits for confirmed handback", async () => {
  const { transport, setState } = fixture();
  render(<BrowserControl transport={transport} pollIntervalMs={20} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Request control" }),
  );
  expect(
    await screen.findByText("Waiting for agent actions to settle"),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Hand back to agent" }),
  ).toBeNull();
  setState({
    ...active,
    control_state: "human_active",
    control_epoch: 1,
    controller_kind: "human",
    controller_id: "panel-one",
    allowed_controls: ["hand_back"],
  });
  await userEvent.click(
    await screen.findByRole("button", { name: "Hand back to agent" }),
  );
  expect(
    await screen.findByText("Refreshing the page before the agent continues"),
  ).toBeVisible();
  expect(screen.queryByRole("button", { name: "Request control" })).toBeNull();
  expect(transport.control).toHaveBeenCalledTimes(2);
  expect(transport.control).toHaveBeenLastCalledWith(
    "private-panel-token",
    { operation: "handback", expectedEpoch: 1 },
    expect.any(String),
    expect.any(AbortSignal),
  );
});
it("does not grant writes or handback to a competing panel", async () => {
  const { transport } = fixture({
    ...active,
    control_state: "human_active",
    controller_kind: "human",
    controller_id: "other-panel",
    allowed_controls: ["hand_back"],
  });
  render(<BrowserControl transport={transport} />);
  expect(await screen.findByText("Another panel has control")).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Hand back to agent" }),
  ).toBeNull();
});
it("closing the view does not hand back or cancel the task", async () => {
  const { transport } = fixture();
  render(<BrowserControl transport={transport} />);
  await screen.findByRole("button", { name: "Request control" });
  await userEvent.click(
    screen.getByRole("button", { name: "Close browser view" }),
  );
  expect(screen.queryByRole("button", { name: "Request control" })).toBeNull();
  expect(transport.control).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "Open browser view" }),
  );
  await screen.findByRole("button", { name: "Request control" });
  expect(transport.issue).toHaveBeenCalledOnce();
});
it("shows disconnected state without automatically retrying an uncertain command", async () => {
  const { transport } = fixture();
  transport.control = vi.fn().mockRejectedValue(new Error("connection lost"));
  render(<BrowserControl transport={transport} />);
  await userEvent.click(
    await screen.findByRole("button", { name: "Request control" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Control request could not be confirmed",
  );
  expect(
    screen.getByRole("button", { name: "Request control" }),
  ).toBeDisabled();
  await userEvent.click(
    screen.getByRole("button", { name: "Reconnect browser view" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Request control" }),
    ).toBeEnabled(),
  );
  expect(transport.control).toHaveBeenCalledOnce();
});
it.each(["canceled", "expired", "completed"])(
  "removes controls and frames when closed: %s",
  async (reason) => {
    const { transport } = fixture({
      ...active,
      control_state: "closed",
      terminal_reason: reason,
      allowed_controls: [],
    });
    render(<BrowserControl transport={transport} />);
    expect(await screen.findByText(`Browser session ${reason}`)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Request control" }),
    ).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  },
);
it("labels stale frames and does not invent a live frame", async () => {
  const { transport } = fixture();
  transport.frame = vi.fn(async () => ({
    frameSequence: 4,
    capturedAt: new Date(Date.now() - 20000).toISOString(),
    controlEpoch: 0,
    viewport: { width: 800, height: 600 },
    imageDataUrl: "data:image/png;base64,AA==",
  }));
  render(<BrowserControl transport={transport} />);
  expect(
    await screen.findByText("Stale frame · interaction disabled"),
  ).toBeVisible();
  expect(
    screen.getByRole("img", { name: "Browser page, last received frame" }),
  ).toBeVisible();
});

it("does not let an older poll overwrite a confirmed command receipt", async () => {
  const { transport } = fixture();
  let release: (value: ControlSnapshot) => void = () => {};
  transport.state = vi.fn(
    () =>
      new Promise<ControlSnapshot>((resolve) => {
        release = resolve;
      }),
  );
  transport.control = vi.fn(async (): Promise<ControlSnapshot> => ({
    ...active,
    control_state: "human_active",
    control_epoch: 1,
    controller_kind: "human",
    controller_id: "panel-one",
    allowed_controls: ["hand_back"],
  }));
  render(<BrowserControl transport={transport} pollIntervalMs={20} />);
  await waitFor(() => expect(transport.state).toHaveBeenCalled());
  await userEvent.click(
    screen.getByRole("button", { name: "Request control" }),
  );
  await screen.findByRole("button", { name: "Hand back to agent" });
  release(active);
  await new Promise((resolve) => setTimeout(resolve, 30));
  expect(
    screen.getByRole("button", { name: "Hand back to agent" }),
  ).toBeVisible();
});

it("does not fetch frames or offer control beyond the absolute deadline", async () => {
  const { transport } = fixture({
    ...active,
    deadline_at: new Date(Date.now() - 1).toISOString(),
  });
  transport.frame = vi.fn(async () => null);
  render(<BrowserControl transport={transport} />);
  expect(await screen.findByText("Browser session expired")).toBeVisible();
  expect(transport.frame).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Request control" })).toBeNull();
});

it("sends one frame-bound human fill and clears the transient value", async () => {
  const { transport } = fixture({
    ...active,
    control_state: "human_active",
    control_epoch: 1,
    controller_kind: "human",
    controller_id: "panel-one",
    allowed_controls: ["hand_back"],
  });
  transport.frame = vi.fn(async () => ({
    frameSequence: 8,
    capturedAt: new Date().toISOString(),
    controlEpoch: 1,
    viewport: { width: 800, height: 600 },
    imageDataUrl: "data:image/jpeg;base64,AA==",
  }));
  transport.humanAction = vi.fn(async () => ({ status: "completed" as const }));
  render(<BrowserControl transport={transport} />);
  await screen.findByLabelText("Text to fill");
  await userEvent.type(screen.getByLabelText("Page X"), "100");
  await userEvent.type(screen.getByLabelText("Page Y"), "120");
  await userEvent.type(
    screen.getByLabelText("Text to fill"),
    "synthetic value",
  );
  await userEvent.click(screen.getByRole("button", { name: "Fill point" }));
  expect(transport.humanAction).toHaveBeenCalledWith(
    "private-panel-token",
    {
      expectedEpoch: 1,
      action: {
        type: "fill",
        x: 100,
        y: 120,
        value: "synthetic value",
        frame_sequence: 8,
        viewport: { width: 800, height: 600 },
      },
    },
    expect.any(String),
    expect.any(AbortSignal),
  );
  expect(transport.humanAction).toHaveBeenCalledOnce();
  expect(screen.getByLabelText("Text to fill")).toHaveValue("");
});
it.each(["receipt", "http409"])(
  "never retries an unknown human action and disables further writes (%s)",
  async (mode) => {
    const { transport } = fixture({
      ...active,
      control_state: "human_active",
      control_epoch: 1,
      controller_kind: "human",
      controller_id: "panel-one",
      allowed_controls: ["hand_back"],
    });
    transport.frame = vi.fn(async () => ({
      frameSequence: 8,
      capturedAt: new Date().toISOString(),
      controlEpoch: 1,
      viewport: { width: 800, height: 600 },
      imageDataUrl: "data:image/jpeg;base64,AA==",
    }));
    transport.humanAction = vi.fn(async () => {
      if (mode === "http409")
        throw Object.assign(new Error("conflict"), { status: 409 });
      return { status: "unknown" as const };
    });
    render(<BrowserControl transport={transport} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Scroll down" }),
    );
    expect(
      await screen.findByText("Action outcome unknown. Do not repeat it."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Scroll down" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Hand back to agent" }),
    ).toBeDisabled();
    expect(transport.humanAction).toHaveBeenCalledOnce();
  },
);
it("disables human page input on a stale frame", async () => {
  const { transport } = fixture({
    ...active,
    control_state: "human_active",
    control_epoch: 1,
    controller_kind: "human",
    controller_id: "panel-one",
    allowed_controls: ["hand_back"],
  });
  transport.frame = vi.fn(async () => ({
    frameSequence: 8,
    capturedAt: new Date(Date.now() - 20000).toISOString(),
    controlEpoch: 1,
    viewport: { width: 800, height: 600 },
    imageDataUrl: "data:image/jpeg;base64,AA==",
  }));
  transport.humanAction = vi.fn(async () => ({ status: "completed" as const }));
  render(<BrowserControl transport={transport} />);
  expect(
    await screen.findByRole("button", { name: "Scroll down" }),
  ).toBeDisabled();
  expect(screen.getByLabelText("Text to fill")).toBeDisabled();
  expect(transport.humanAction).not.toHaveBeenCalled();
});

it("clears unsent text when closing the browser view", async () => {
  const { transport } = fixture({
    ...active,
    control_state: "human_active",
    control_epoch: 1,
    controller_kind: "human",
    controller_id: "panel-one",
    allowed_controls: ["hand_back"],
  });
  transport.frame = vi.fn(async () => ({
    frameSequence: 8,
    capturedAt: new Date().toISOString(),
    controlEpoch: 1,
    viewport: { width: 800, height: 600 },
    imageDataUrl: "data:image/jpeg;base64,AA==",
  }));
  transport.humanAction = vi.fn(async () => ({ status: "completed" as const }));
  render(<BrowserControl transport={transport} />);
  await userEvent.type(
    await screen.findByLabelText("Text to fill"),
    "unsent synthetic",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Close browser view" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Open browser view" }),
  );
  expect(await screen.findByLabelText("Text to fill")).toHaveValue("");
  expect(transport.humanAction).not.toHaveBeenCalled();
});
