import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TapProductPrototype } from "./TapProductPrototype";
import { resolveComposerAttachments } from "./prototype/composerAttachments";
import type { LibrarySource } from "./prototype/model";
import { setPrototypeFaults } from "./prototype/prototypeFaults";

const HEALTH_QUESTION = "What does the health disclosure rule require?";
const BDD_REQUEST = "Create BDD test cases for life insurance underwriting";

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.useRealTimers();
  setPrototypeFaults([]);
});

const advance = (ms: number) =>
  act(() => {
    vi.advanceTimersByTime(ms);
  });

const composer = () =>
  screen.getByLabelText("Message Tapper") as HTMLTextAreaElement;

function type(text: string) {
  fireEvent.change(composer(), { target: { value: text } });
}

function send(text: string) {
  type(text);
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  advance(400);
}

function selectUnderwritingSource() {
  fireEvent.click(
    screen.getByRole("checkbox", { name: /Underwriting test rules\.pdf/ }),
  );
}

function userMessages() {
  return Array.from(document.querySelectorAll(".tap-user-message")).map(
    (element) => element.textContent,
  );
}

function history() {
  return screen.getByRole("navigation", { name: "Chat history" });
}

function createTwoConversations() {
  selectUnderwritingSource();
  send(HEALTH_QUESTION);
  advance(1800);
  fireEvent.click(screen.getByRole("button", { name: "New chat" }));
  send(BDD_REQUEST);
}

describe("chat history controls", () => {
  it("renames a conversation from a keyboard-accessible row menu", () => {
    render(<TapProductPrototype />);
    send(BDD_REQUEST);
    const trigger = within(history()).getByRole("button", {
      name: `More options for ${BDD_REQUEST}`,
    });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");

    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const menu = screen.getByRole("menu", {
      name: `More options for ${BDD_REQUEST}`,
    });
    expect(within(menu).getByRole("menuitem", { name: "Rename" })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(within(menu).getByRole("menuitem", { name: "Delete" })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "Home" });
    expect(within(menu).getByRole("menuitem", { name: "Rename" })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const nameInput = screen.getByRole("textbox", { name: "Chat name" });
    expect(nameInput).toHaveFocus();
    expect(nameInput).toHaveValue(BDD_REQUEST);
    expect(nameInput).toHaveAttribute("maxLength", "120");

    fireEvent.change(nameInput, { target: { value: "   " } });
    fireEvent.keyDown(nameInput, { key: "Enter" });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Enter a name of 1–120 characters.",
    );

    fireEvent.change(nameInput, { target: { value: "  Underwriting BDD  " } });
    fireEvent.keyDown(nameInput, { key: "Enter" });
    expect(
      within(history()).getByRole("button", { name: "Underwriting BDD" }),
    ).toBeVisible();

    fireEvent.click(
      within(history()).getByRole("button", {
        name: "More options for Underwriting BDD",
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Chat name" }), {
      target: { value: "Discarded name" },
    });
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Chat name" }), {
      key: "Escape",
    });
    expect(
      within(history()).getByRole("button", { name: "Underwriting BDD" }),
    ).toBeVisible();
    expect(screen.queryByText("Discarded name")).toBeNull();
  });

  it("confirms deletion and keeps or resets the current view", () => {
    render(<TapProductPrototype />);
    createTwoConversations();

    fireEvent.click(
      within(history()).getByRole("button", {
        name: `More options for ${HEALTH_QUESTION}`,
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = screen.getByRole("dialog", { name: "Delete chat?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(
      within(history()).getByRole("button", { name: HEALTH_QUESTION }),
    ).toBeVisible();

    fireEvent.click(
      within(history()).getByRole("button", {
        name: `More options for ${HEALTH_QUESTION}`,
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Delete chat?" })).getByRole(
        "button",
        { name: "Delete" },
      ),
    );
    expect(
      within(history()).queryByRole("button", { name: HEALTH_QUESTION }),
    ).toBeNull();
    expect(userMessages()).toEqual([BDD_REQUEST]);

    fireEvent.click(
      within(history()).getByRole("button", {
        name: `More options for ${BDD_REQUEST}`,
      }),
    );
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Delete chat?" })).getByRole(
        "button",
        { name: "Delete" },
      ),
    );
    expect(
      screen.getByRole("heading", { name: "What can I do for you?" }),
    ).toBeVisible();
    expect(userMessages()).toEqual([]);
    expect(
      within(history()).queryByRole("button", { name: BDD_REQUEST }),
    ).toBeNull();
    expect(
      within(history()).queryByRole("button", { name: HEALTH_QUESTION }),
    ).toBeNull();
  });

  it("filters chat history by title without case sensitivity", () => {
    render(<TapProductPrototype />);
    createTwoConversations();
    const search = within(history()).getByRole("searchbox", {
      name: "Search chats",
    });

    fireEvent.change(search, { target: { value: "bDd" } });
    expect(
      within(history()).getByRole("button", { name: BDD_REQUEST }),
    ).toBeVisible();
    expect(
      within(history()).queryByRole("button", { name: HEALTH_QUESTION }),
    ).toBeNull();

    fireEvent.change(search, { target: { value: "zzznomatch" } });
    expect(within(history()).getByText("No matching chats")).toBeVisible();
  });
});

describe("composer and turn controls", () => {
  it("shows a busy send state, prevents double submit and stops generation", () => {
    render(<TapProductPrototype />);
    type(HEALTH_QUESTION);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    const busy = screen.getByRole("button", { name: "Sending" });
    expect(busy).toBeDisabled();
    expect(busy).toHaveAttribute("aria-busy", "true");
    type(HEALTH_QUESTION);
    fireEvent.submit(composer().closest("form")!);
    advance(400);
    expect(userMessages()).toEqual([HEALTH_QUESTION]);

    fireEvent.submit(composer().closest("form")!);
    advance(400);
    expect(userMessages()).toEqual([HEALTH_QUESTION]);
    expect(composer()).toHaveValue(HEALTH_QUESTION);

    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(screen.getByText("Generation stopped.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Stop generating" })).toBeNull();
  });

  it("keeps the in-answer stop consistent with the composer", () => {
    render(<TapProductPrototype />);
    send(HEALTH_QUESTION);
    expect(
      screen.getByRole("button", { name: "Stop generating" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(screen.getByText("Generation stopped.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Stop generating" })).toBeNull();
  });

  it("retries a stopped reply as a new turn with the same context", () => {
    render(<TapProductPrototype />);
    selectUnderwritingSource();
    send(HEALTH_QUESTION);
    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    advance(400);
    advance(1800);
    expect(userMessages()).toEqual([HEALTH_QUESTION, HEALTH_QUESTION]);
    expect(screen.getByText("Generation stopped.")).toBeVisible();
    expect(screen.getAllByText("Message context · 1")).toHaveLength(2);
    expect(
      screen.getByText(/Block submission when health disclosure is missing/),
    ).toBeVisible();
  });

  it("retries a failed reply as a new turn", () => {
    const view = render(<TapProductPrototype />);
    send(HEALTH_QUESTION);
    view.unmount();
    render(<TapProductPrototype />);
    expect(
      screen.getByText("The answer could not be generated. Please try again."),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    advance(400);
    expect(userMessages()).toEqual([HEALTH_QUESTION, HEALTH_QUESTION]);
    expect(
      screen.getByText("The answer could not be generated. Please try again."),
    ).toBeVisible();
  });

  it("regenerates a completed answer as a new turn without rewriting history", () => {
    render(<TapProductPrototype />);
    selectUnderwritingSource();
    send(HEALTH_QUESTION);
    advance(1800);
    expect(
      screen.queryByRole("button", { name: "Stop generating" }),
    ).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    advance(400);
    expect(screen.getByRole("button", { name: "Regenerate" })).toBeDisabled();
    advance(1800);
    expect(userMessages()).toEqual([HEALTH_QUESTION, HEALTH_QUESTION]);
    expect(
      screen.getAllByText(/Block submission when health disclosure is missing/),
    ).toHaveLength(2);
    expect(screen.getAllByText("Message context · 1")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Regenerate" })).toHaveLength(2);
  });

  it("offers regenerate for an abstained answer", () => {
    render(<TapProductPrototype />);
    send("What is the claims turnaround?");
    advance(1800);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    advance(400);
    expect(userMessages()).toHaveLength(2);
  });

  it("edits a question in the composer with its context and appends a new turn", () => {
    render(<TapProductPrototype />);
    selectUnderwritingSource();
    send(HEALTH_QUESTION);
    advance(1800);
    expect(
      screen.queryByRole("button", { name: "Remove Underwriting test rules.pdf" }),
    ).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Edit question" }));
    expect(composer()).toHaveValue(HEALTH_QUESTION);
    expect(composer()).toHaveFocus();
    expect(
      screen.getByRole("button", { name: "Remove Underwriting test rules.pdf" }),
    ).toBeVisible();

    type("What does the health disclosure rule require for minors?");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    advance(400);
    expect(userMessages()).toEqual([
      HEALTH_QUESTION,
      "What does the health disclosure rule require for minors?",
    ]);
    expect(screen.getAllByText("Message context · 1")).toHaveLength(2);
  });

  it("uploads a file to Library and adds it to the message once searchable", () => {
    render(<TapProductPrototype />);
    fireEvent.click(screen.getByRole("button", { name: "Add to message" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Upload file" }));
    const input = screen.getByLabelText("Upload file") as HTMLInputElement;
    expect(input.accept).toBe(".pdf,.docx,.md,.txt");

    fireEvent.change(input, {
      target: { files: [new File(["x"], "scan.png", { type: "image/png" })] },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Upload a PDF, DOCX, MD or TXT file.",
    );

    fireEvent.change(input, {
      target: { files: [new File(["x"], "setup.exe")] },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This file type isn’t supported.",
    );
    const large = new File(["x"], "large.pdf");
    Object.defineProperty(large, "size", { value: 25 * 1024 * 1024 + 1 });
    fireEvent.change(input, { target: { files: [large] } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Files must be 25 MB or smaller.",
    );

    fireEvent.change(input, {
      target: {
        files: [new File(["# notes"], "Claims notes.md", { type: "text/markdown" })],
      },
    });
    expect(screen.queryByRole("alert")).toBeNull();
    // Text sources index directly, so a searchable upload joins this message.
    expect(screen.queryByRole("dialog", { name: "Document review" })).toBeNull();
    expect(
      within(screen.getByRole("group", { name: "Message context" })).getByText(
        "Claims notes.md",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("group", { name: "Attachments" })).toBeNull();
  });

  it("adds a published attachment to the message context", () => {
    const source = (reviewState: LibrarySource["reviewState"]) =>
      ({
        id: "doc-1",
        name: "Claims notes.md",
        origin: "knowledge-base",
        type: "MD",
        status: reviewState === "published" ? "ready" : "processing",
        reviewState,
        description: "",
      }) satisfies LibrarySource;
    const attachment = { conversationId: "chat-1", sourceId: "doc-1" };

    expect(
      resolveComposerAttachments([attachment], [source("processing")]),
    ).toEqual({
      pending: [
        {
          conversationId: "chat-1",
          id: "doc-1",
          name: "Claims notes.md",
          type: "MD",
          status: "processing",
        },
      ],
      ready: [],
    });
    expect(
      resolveComposerAttachments([attachment], [source("reviewing")]).pending[0]
        ?.status,
    ).toBe("needs-review");
    expect(
      resolveComposerAttachments([attachment], [source("published")]),
    ).toEqual({ pending: [], ready: [attachment] });
  });

  it("keeps the draft when sending fails", () => {
    setPrototypeFaults(["send-failed"]);
    render(<TapProductPrototype />);
    type(HEALTH_QUESTION);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Message was not sent.",
    );
    expect(composer()).toHaveValue(HEALTH_QUESTION);
    expect(userMessages()).toEqual([]);

    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    advance(400);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(composer()).toHaveValue("");
    expect(userMessages()).toEqual([HEALTH_QUESTION]);
  });

  it("reports a failed stop", () => {
    render(<TapProductPrototype />);
    setPrototypeFaults(["stop-failed"]);
    send(HEALTH_QUESTION);
    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "The response may still be running.",
    );
    expect(
      screen.getByRole("button", { name: "Stop generating" }),
    ).toBeVisible();
    advance(3000);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("disables unavailable models", () => {
    render(<TapProductPrototype />);
    fireEvent.click(
      screen.getByRole("button", { name: /Select model/ }),
    );
    const option = screen.getByRole("menuitemradio", { name: /GPT-5.4/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    expect(option).toHaveTextContent("Unavailable");
    fireEvent.click(option);
    expect(
      screen.getByRole("button", { name: /Select model/ }),
    ).toHaveTextContent("GPT-5.6 Sol");
  });

  it("blocks sending when no model is available", () => {
    setPrototypeFaults(["no-models"]);
    render(<TapProductPrototype />);
    expect(
      screen.getByRole("button", { name: /Select model/ }),
    ).toHaveTextContent("No models available");
    type(HEALTH_QUESTION);
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("explains that each turn records context", () => {
    render(<TapProductPrototype />);
    expect(
      screen.getByText(
        "Each turn records the knowledge context you select.",
      ),
    ).toBeVisible();
  });
});

it("localizes the chat controls in Chinese", () => {
  render(<TapProductPrototype />);
  fireEvent.click(screen.getByRole("button", { name: "中文" }));
  fireEvent.change(screen.getByLabelText("向 Tapper 发送消息"), {
    target: { value: "健康告知有什么要求？" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  expect(screen.getByRole("button", { name: "正在发送" })).toBeDisabled();
  advance(400);
  // The composer and the in-answer stop share the same localized action.
  expect(screen.getAllByRole("button", { name: "停止生成" })).toHaveLength(2);
  expect(screen.getByRole("button", { name: "编辑问题" })).toBeVisible();
  expect(
    screen.getByRole("searchbox", { name: "搜索对话" }),
  ).toBeVisible();
  expect(
    screen.getByRole("button", { name: "更多操作：健康告知有什么要求？" }),
  ).toBeVisible();
});
