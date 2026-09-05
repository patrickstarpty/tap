import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  document,
  fakeKnowledgeClient,
} from "../../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../../features/knowledge/testing/renderKnowledgeApp";
import { TapProductPrototype } from "./TapProductPrototype";
import { createInitialArtifactState } from "./prototype/artifacts/fixtures";
import {
  PROTOTYPE_SNAPSHOT_VERSION,
  writePrototypeSnapshot,
} from "./prototype/artifacts/persistence";
import { appendTurn, createConversation } from "./prototype/model";

const prototypeStyles = readFileSync(
  resolve("src/widgets/tap/TapProductPrototype.css"),
  "utf8",
);

function renderPrototype() {
  const api = fakeKnowledgeClient().withDocuments([
    document({
      documentId: "life-underwriting-rules",
      filename: "life-underwriting-rules.md",
      stage: "ready",
      status: "ready",
    }),
    document({
      documentId: "health-disclosure-guide",
      filename: "health-disclosure-guide.pdf",
      stage: "ready",
      status: "ready",
    }),
  ]);

  return renderKnowledgeApp(<TapProductPrototype />, { api });
}

function installPrototypeStyles() {
  const style = window.document.createElement("style");
  style.textContent = prototypeStyles;
  window.document.head.append(style);
  return style;
}

function renderPrototypeWithQuestions(total: number) {
  let conversation = createConversation("chat-1");
  for (let index = 1; index <= total; index += 1) {
    conversation = appendTurn(conversation, {
      id: `turn-${index}`,
      intent: "answer",
      locale: "en",
      modelId: conversation.modelId,
      prompt: `Question ${index}`,
      sourceReferences: [],
    });
  }
  writePrototypeSnapshot(window.localStorage, {
    version: PROTOTYPE_SNAPSHOT_VERSION,
    activeConversationId: conversation.id,
    conversations: [conversation],
    artifacts: createInitialArtifactState(),
  });
  return renderPrototype();
}

function renderPrototypeWithManyDocuments() {
  const api = fakeKnowledgeClient().withDocuments([
    document({
      documentId: "life-underwriting-rules",
      filename: "life-underwriting-rules.md",
      stage: "ready",
      status: "ready",
    }),
    document({
      documentId: "health-disclosure-guide",
      filename: "health-disclosure-guide.pdf",
      stage: "ready",
      status: "ready",
    }),
    document({
      documentId: "application-checklist",
      filename: "application-checklist.docx",
      stage: "ready",
      status: "ready",
    }),
    document({
      documentId: "underwriting-evidence",
      filename: "underwriting-evidence.txt",
      stage: "ready",
      status: "ready",
    }),
    document({
      documentId: "beneficiary-guide",
      filename: "beneficiary-guide.md",
      stage: "ready",
      status: "ready",
    }),
  ]);

  return renderKnowledgeApp(<TapProductPrototype />, { api });
}

function renderPrototypeWithLibraryStatuses() {
  const api = fakeKnowledgeClient().withDocuments([
    document({
      documentId: "life-underwriting-rules",
      filename: "life-underwriting-rules.md",
      stage: "ready",
      status: "ready",
    }),
    document({
      documentId: "health-disclosure-guide",
      filename: "health-disclosure-guide.pdf",
      stage: "embedding",
      status: "failed",
    }),
    document({
      documentId: "application-checklist",
      filename: "application-checklist.docx",
      stage: "parsing",
      status: "processing",
    }),
    document({
      documentId: "beneficiary-guide",
      filename: "beneficiary-guide.txt",
      stage: "ready",
      status: "ready",
    }),
  ]);

  return renderKnowledgeApp(<TapProductPrototype />, { api });
}

function mockNarrowViewport() {
  return vi
    .spyOn(window, "matchMedia")
    .mockImplementation((query): MediaQueryList => ({
      matches: query === "(max-width: 640px)" || query === "(max-width: 820px)",
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => true,
    }));
}

describe("Tap product prototype interactions", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("defaults to English and lets the user switch the interface language", async () => {
    const user = userEvent.setup();
    renderPrototype();

    expect(
      screen.getByRole("button", { name: "English", pressed: true }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "What can I do for you?" }),
    ).toBeVisible();
    expect(
      screen.getByPlaceholderText("Ask about life insurance or testing..."),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Ask about life insurance, create BDD test cases, or build an automation.",
      ),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "中文" }));

    expect(
      screen.getByRole("button", { name: "中文", pressed: true }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "我能为您做什么？" }),
    ).toBeVisible();
    expect(
      screen.getByPlaceholderText("询问寿险业务或测试问题..."),
    ).toBeVisible();
  });

  it("synchronizes the document language and restores the host value on unmount", async () => {
    const user = userEvent.setup();
    const originalLanguage = globalThis.document.documentElement.lang;
    globalThis.document.documentElement.lang = "fr";
    const view = renderPrototype();

    expect(globalThis.document.documentElement.lang).toBe("en");
    await user.click(screen.getByRole("button", { name: "中文" }));
    expect(globalThis.document.documentElement.lang).toBe("zh-CN");

    view.unmount();
    expect(globalThis.document.documentElement.lang).toBe("fr");
    globalThis.document.documentElement.lang = originalLanguage;
  });

  it("fills and focuses the composer when a suggested underwriting prompt is chosen", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const composer = screen.getByRole("textbox", { name: "Message Tapper" });
    const prompt = "Summarize the life insurance underwriting rules";
    await user.click(screen.getByRole("button", { name: prompt }));

    expect(composer).toHaveValue(prompt);
    expect(composer).toHaveFocus();
    expect(screen.queryByRole("log", { name: "Conversation" })).toBeNull();
  });

  it("separates the product rail from the collapsible Tapper sidebar", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const productRail = screen.getByRole("complementary", {
      name: "Product",
    });
    const navigation = within(productRail).getByRole("navigation", {
      name: "Product",
    });
    expect(
      within(navigation)
        .getAllByRole("button")
        .map((item) => item.getAttribute("aria-label")),
    ).toEqual(["Tapper", "Test Management", "Low Code Automation"]);
    const tapperSidebar = screen.getByRole("complementary", {
      name: "Tapper tools",
    });
    const tapperNavigation = within(tapperSidebar).getByRole("navigation", {
      name: "Tapper tools",
    });
    expect(
      within(tapperNavigation)
        .getAllByRole("button")
        .map((item) => item.textContent?.trim()),
    ).toEqual(["New chat", "Agent", "Skills", "Library"]);
    const newChatButton = within(tapperNavigation).getByRole("button", {
      name: "New chat",
    });
    expect(newChatButton.querySelector(".anticon-form")).toBeVisible();
    expect(newChatButton.querySelector(".anticon-plus")).toBeNull();
    expect(
      within(tapperNavigation)
        .getAllByRole("button")
        .map((item) => item.className),
    ).toEqual([
      "tap-navigation-item tap-navigation-item--tapper",
      "tap-navigation-item tap-navigation-item--tapper",
      "tap-navigation-item tap-navigation-item--tapper",
      "tap-navigation-item tap-navigation-item--tapper",
    ]);

    const collapseSidebar = screen.getByRole("button", {
      name: "Collapse sidebar",
    });
    expect(
      collapseSidebar.querySelector('[data-panel-icon="left"]'),
    ).toHaveAttribute("data-panel-state", "expanded");
    await user.click(collapseSidebar);
    expect(
      screen.queryByRole("complementary", { name: "Tapper tools" }),
    ).not.toBeInTheDocument();
    const expandSidebar = screen.getByRole("button", {
      name: "Expand sidebar",
    });
    expect(expandSidebar).toHaveFocus();
    expect(
      expandSidebar.querySelector('[data-panel-icon="left"]'),
    ).toHaveAttribute("data-panel-state", "collapsed");

    await user.click(expandSidebar);
    const restoredSidebar = screen.getByRole("complementary", {
      name: "Tapper tools",
    });
    expect(restoredSidebar).toBeVisible();
    expect(
      within(restoredSidebar).getByRole("button", {
        name: "Collapse sidebar",
      }),
    ).toHaveFocus();
  });

  it("marks New chat as current only while the chat destination is active", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const newChatButton = screen.getByRole("button", { name: "New chat" });
    expect(newChatButton).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Agent" }));
    expect(newChatButton).not.toHaveAttribute("aria-current");

    await user.click(newChatButton);
    expect(newChatButton).toHaveAttribute("aria-current", "page");
  });

  it("shows Tapper tools only while the Tapper workspace is active", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const tapperButton = screen.getByRole("button", { name: "Tapper" });
    expect(tapperButton).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("navigation", { name: "Tapper tools" }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Test Management" }));

    expect(tapperButton).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("navigation", { name: "Tapper tools" }),
    ).not.toBeInTheDocument();

    await user.click(tapperButton);

    expect(tapperButton).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("navigation", { name: "Tapper tools" }),
    ).toBeVisible();
  });

  it("restores the last Tapper surface when the workspace is reopened", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Skills" }));
    expect(screen.getByRole("heading", { name: "Skills" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Tapper" }));

    expect(screen.getByRole("heading", { name: "Skills" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Skills" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("opens Tapper tools as an inert mobile drawer and restores focus when dismissed", async () => {
    const matchMedia = mockNarrowViewport();

    try {
      const user = userEvent.setup();
      renderPrototype();
      const productRail = screen.getByRole("complementary", {
        name: "Product",
      });
      const tapperButton = within(productRail).getByRole("button", {
        name: "Tapper",
      });
      const main = screen.getByRole("main");

      expect(
        screen.queryByRole("complementary", { name: "Tapper tools" }),
      ).not.toBeInTheDocument();
      expect(main).not.toHaveAttribute("aria-hidden");

      await user.click(tapperButton);

      const collapseButton = screen.getByRole("button", {
        name: "Collapse sidebar",
      });
      expect(
        screen.getByRole("complementary", { name: "Tapper tools" }),
      ).toBeVisible();
      expect(productRail).toBeVisible();
      expect(main).toHaveAttribute("aria-hidden", "true");
      expect(main).toHaveAttribute("inert");
      expect(globalThis.document.body).toHaveStyle({ overflow: "hidden" });

      collapseButton.focus();
      await user.keyboard("{Escape}");

      expect(
        screen.queryByRole("complementary", { name: "Tapper tools" }),
      ).not.toBeInTheDocument();
      expect(main).not.toHaveAttribute("aria-hidden");
      expect(main).not.toHaveAttribute("inert");
      expect(globalThis.document.body).not.toHaveStyle({
        overflow: "hidden",
      });
      const expandSidebar = screen.getByRole("button", {
        name: "Expand sidebar",
      });
      expect(expandSidebar).toHaveFocus();

      await user.click(expandSidebar);
      await user.click(screen.getByRole("button", { name: "Close sidebar" }));
      expect(
        screen.getByRole("button", { name: "Expand sidebar" }),
      ).toHaveFocus();

      await user.click(tapperButton);
      await user.click(screen.getByRole("button", { name: "New chat" }));
      expect(
        screen.queryByRole("complementary", { name: "Tapper tools" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("textbox", { name: "Message Tapper" }),
      ).toHaveFocus();
    } finally {
      matchMedia.mockRestore();
    }
  });

  it("moves focus to a mobile Tapper destination and restores that surface", async () => {
    const matchMedia = mockNarrowViewport();

    try {
      const user = userEvent.setup();
      renderPrototype();
      const tapperButton = screen.getByRole("button", { name: "Tapper" });

      await user.click(tapperButton);
      await user.click(screen.getByRole("button", { name: "Skills" }));

      expect(
        screen.queryByRole("complementary", { name: "Tapper tools" }),
      ).not.toBeInTheDocument();
      const skillsHeading = screen.getByRole("heading", { name: "Skills" });
      expect(skillsHeading).toHaveFocus();

      await user.click(tapperButton);

      expect(
        screen.getByRole("complementary", { name: "Tapper tools" }),
      ).toBeVisible();
      expect(skillsHeading).toBeVisible();
      expect(screen.getByRole("button", { name: "Skills" })).toHaveAttribute(
        "aria-current",
        "page",
      );
    } finally {
      matchMedia.mockRestore();
    }
  });

  it("keeps the compact Tapper and Knowledge sources drawers mutually exclusive", async () => {
    const matchMedia = mockNarrowViewport();

    try {
      const user = userEvent.setup();
      renderPrototype();
      const tapperButton = within(
        screen.getByRole("complementary", { name: "Product" }),
      ).getByRole("button", { name: "Tapper" });

      await user.click(
        screen.getByRole("button", { name: "Expand Knowledge sources" }),
      );
      expect(
        screen.getByRole("complementary", { name: "Knowledge sources" }),
      ).toBeVisible();

      await user.click(tapperButton);
      await user.click(
        screen.getByRole("button", { name: "Collapse sidebar" }),
      );

      expect(
        screen.queryByRole("complementary", { name: "Knowledge sources" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Expand Knowledge sources" }),
      ).toBeVisible();
    } finally {
      matchMedia.mockRestore();
    }
  });

  it("keeps each answer in its response language through locale changes and history navigation", async () => {
    const user = userEvent.setup();
    renderPrototype();
    const englishPrompt = "What evidence is needed for life underwriting?";

    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      englishPrompt,
    );
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(
      screen.getByText(/No knowledge context was selected for this turn/),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "中文" }));
    expect(
      screen.getByText(/No knowledge context was selected for this turn/),
    ).toBeVisible();
    expect(screen.queryByText(/此轮对话未选择知识上下文/)).toBeNull();

    await user.click(screen.getByRole("button", { name: "新建对话" }));
    await user.type(
      screen.getByRole("textbox", { name: "向 Tapper 发送消息" }),
      "寿险投保需要什么资料？",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));
    expect(screen.getByText(/此轮对话未选择知识上下文/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "新建对话" }));
    await user.click(
      within(screen.getByRole("navigation", { name: "对话历史" })).getByRole(
        "button",
        { name: `${englishPrompt} · 对话 1` },
      ),
    );
    expect(
      screen.getByText(/No knowledge context was selected for this turn/),
    ).toBeVisible();
    expect(screen.queryByText(/此轮对话未选择知识上下文/)).toBeNull();
  });

  it("marks every persisted turn with the language used when it was sent", async () => {
    const user = userEvent.setup();
    const { container } = renderPrototype();

    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      "What evidence is needed for life underwriting?",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.click(screen.getByRole("button", { name: "中文" }));
    await user.type(
      screen.getByRole("textbox", { name: "向 Tapper 发送消息" }),
      "寿险投保需要什么资料？",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    const turns = container.querySelectorAll(".tap-turn");
    expect(turns).toHaveLength(2);
    expect(turns[0]).toHaveAttribute("lang", "en");
    expect(turns[1]).toHaveAttribute("lang", "zh-CN");
  });

  it("starts a new empty chat while preserving and restoring earlier life-underwriting chats", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const message = "What evidence is needed for life insurance underwriting?";
    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      message,
    );
    await user.keyboard("{Enter}");
    expect(screen.getByText(message)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(
      screen.getByRole("region", { name: "Start a conversation" }),
    ).toBeVisible();

    const history = screen.getByRole("navigation", { name: "Chat history" });
    await user.click(
      within(history).getByRole("button", {
        name: `${message} · Conversation 1`,
      }),
    );
    expect(screen.getByRole("log", { name: "Conversation" })).toBeVisible();
    expect(screen.getByText(message)).toBeVisible();
  });

  it("recalls the latest sent prompt with ArrowUp only when the composer is empty", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const composer = screen.getByRole("textbox", { name: "Message Tapper" });
    const latestPrompt = "Review the beneficiary evidence";

    await user.type(composer, "Summarize the underwriting rules");
    await user.keyboard("{Enter}");
    await user.type(composer, latestPrompt);
    await user.keyboard("{Enter}");

    expect(composer).toHaveValue("");
    await user.keyboard("{ArrowUp}");
    expect(composer).toHaveValue(latestPrompt);

    await user.clear(composer);
    await user.type(composer, "Keep this draft");
    await user.keyboard("{ArrowUp}");
    expect(composer).toHaveValue("Keep this draft");
  });

  it("restores source, Agent, and Skill context from a context-only session", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const addContext = async (
      menuItem: string,
      dialogName: string,
      optionName: string,
    ) => {
      await user.click(screen.getByRole("button", { name: "Add to message" }));
      await user.click(
        within(screen.getByRole("menu", { name: "Add to message" })).getByRole(
          "menuitem",
          { name: menuItem },
        ),
      );
      const picker = screen.getByRole("dialog", { name: dialogName });
      await user.click(
        await within(picker).findByRole("option", { name: optionName }),
      );
    };

    await addContext(
      "Add from Library",
      "Add from Library",
      "life-underwriting-rules.md",
    );
    await addContext("Use Agents", "Use Agents", "Life Underwriting Analyst");
    await addContext("Use Skills", "Use Skills", "BDD Scenario Design");

    await user.click(screen.getByRole("button", { name: "New chat" }));

    const emptyComposer = screen.getByRole("form", {
      name: "Message composer",
    });
    expect(
      within(emptyComposer).queryByText("life-underwriting-rules.md"),
    ).toBeNull();
    expect(
      within(emptyComposer).queryByText("Life Underwriting Analyst"),
    ).toBeNull();
    expect(within(emptyComposer).queryByText("BDD Scenario Design")).toBeNull();

    const history = screen.getByRole("navigation", { name: "Chat history" });
    await user.click(
      within(history).getByRole("button", {
        name: "New chat · Conversation 1 · 3 selected",
      }),
    );

    const restoredComposer = screen.getByRole("form", {
      name: "Message composer",
    });
    expect(
      within(restoredComposer).getByText("life-underwriting-rules.md"),
    ).toBeVisible();
    expect(
      within(restoredComposer).getByText("Life Underwriting Analyst"),
    ).toBeVisible();
    expect(
      within(restoredComposer).getByText("BDD Scenario Design"),
    ).toBeVisible();
  });

  it("removes selected Knowledge, Agent, and Skill context from the composer", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const addContext = async (
      menuItem: string,
      dialogName: string,
      optionName: string,
    ) => {
      await user.click(screen.getByRole("button", { name: "Add to message" }));
      await user.click(
        within(screen.getByRole("menu", { name: "Add to message" })).getByRole(
          "menuitem",
          { name: menuItem },
        ),
      );
      await user.click(
        within(screen.getByRole("dialog", { name: dialogName })).getByRole(
          "option",
          { name: optionName },
        ),
      );
    };

    await addContext(
      "Add from Library",
      "Add from Library",
      "life-underwriting-rules.md",
    );
    await addContext("Use Agents", "Use Agents", "Life Underwriting Analyst");
    await addContext("Use Skills", "Use Skills", "BDD Scenario Design");

    const composer = screen.getByRole("form", { name: "Message composer" });
    for (const label of [
      "life-underwriting-rules.md",
      "Life Underwriting Analyst",
      "BDD Scenario Design",
    ]) {
      await user.click(
        within(composer).getByRole("button", { name: `Remove ${label}` }),
      );
      expect(within(composer).queryByText(label)).toBeNull();
    }

    expect(
      within(composer).queryByRole("group", { name: "Message context" }),
    ).toBeNull();
  });

  it("uses a Codex-style model-only selector in the Tapper composer", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const composer = screen.getByRole("form", { name: "Message composer" });
    const trigger = within(composer).getByRole("button", {
      name: "Select model, current model GPT-5.6 Sol",
    });

    expect(trigger).toHaveTextContent("GPT-5.6 Sol");
    expect(trigger.querySelector(".anticon-thunderbolt")).toBeNull();
    expect(within(composer).queryByText(/Fast|Ultra/)).toBeNull();

    await user.click(trigger);

    const menu = screen.getByRole("menu", { name: "Models" });
    expect(
      within(menu)
        .getAllByRole("menuitemradio")
        .map((option) => option.textContent?.trim()),
    ).toEqual([
      "GPT-5.6 Sol",
      "GPT-5.6 Terra",
      "GPT-5.6 Luna",
      "GPT-5.5",
      "GPT-5.4",
    ]);
    expect(within(menu).queryByText(/Fast|Ultra/)).toBeNull();

    await user.click(
      within(menu).getByRole("menuitemradio", { name: "GPT-5.6 Terra" }),
    );

    expect(
      within(composer).getByRole("button", {
        name: "Select model, current model GPT-5.6 Terra",
      }),
    ).toBeVisible();
    expect(screen.queryByRole("menu", { name: "Models" })).toBeNull();
  });

  it("closes the model menu without stealing focus from the composer", async () => {
    const user = userEvent.setup();
    renderPrototype();
    const composer = screen.getByRole("textbox", { name: "Message Tapper" });

    await user.click(
      screen.getByRole("button", {
        name: "Select model, current model GPT-5.6 Sol",
      }),
    );
    await user.click(composer);

    expect(screen.queryByRole("menu", { name: "Models" })).toBeNull();
    expect(composer).toHaveFocus();
  });

  it("keeps the selected model with its Conversation", async () => {
    const user = userEvent.setup();
    renderPrototype();
    const prompt = "Review the underwriting evidence";

    await user.click(
      screen.getByRole("button", {
        name: "Select model, current model GPT-5.6 Sol",
      }),
    );
    await user.click(
      screen.getByRole("menuitemradio", { name: "GPT-5.6 Luna" }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      prompt,
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(
      screen.getByRole("button", {
        name: "Select model, current model GPT-5.6 Sol",
      }),
    ).toBeVisible();

    await user.click(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${prompt} · Conversation 1` }),
    );
    expect(
      screen.getByRole("button", {
        name: "Select model, current model GPT-5.6 Luna",
      }),
    ).toBeVisible();
  });

  it("uses the Tapper ink mark and wordmark in the product shell", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const productRail = screen.getByRole("complementary", { name: "Product" });
    const tapperButton = within(productRail).getByRole("button", {
      name: "Tapper",
    });
    const railMark = tapperButton.querySelector(
      'img[src*="tapper-mark-ink.svg"]',
    );
    expect(railMark).toBeVisible();
    expect(prototypeStyles).toMatch(
      /^\.tap-tapper-rail-mark\s*\{[^}]*width:\s*24px;[^}]*height:\s*24px;/m,
    );
    const tapperHeading = screen.getByRole("heading", { name: "Tapper" });
    expect(
      tapperHeading.querySelector('img[src*="tapper-wordmark-ink.svg"]'),
    ).not.toBeNull();

    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      "What evidence is needed?",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    await user.click(
      within(productRail).getByRole("button", { name: "Test Management" }),
    );
    expect(railMark).toBeVisible();
  });

  it("collapses and restores Knowledge sources without losing its selection", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const sources = screen.getByRole("complementary", {
      name: "Knowledge sources",
    });
    const source = await within(sources).findByRole("checkbox", {
      name: /life-underwriting-rules\.md/,
    });
    await user.click(source);

    const collapseSources = within(sources).getByRole("button", {
      name: "Collapse Knowledge sources",
    });
    expect(
      collapseSources.querySelector('[data-panel-icon="right"]'),
    ).toHaveAttribute("data-panel-state", "expanded");
    await user.click(collapseSources);

    expect(
      screen.queryByRole("complementary", { name: "Knowledge sources" }),
    ).not.toBeInTheDocument();
    const expandSources = screen.getByRole("button", {
      name: "Expand Knowledge sources",
    });
    expect(expandSources).toHaveFocus();
    expect(
      expandSources.querySelector('[data-panel-icon="right"]'),
    ).toHaveAttribute("data-panel-state", "collapsed");

    await user.click(expandSources);
    const restoredSources = screen.getByRole("complementary", {
      name: "Knowledge sources",
    });
    expect(restoredSources).toBeVisible();
    expect(
      within(restoredSources).getByRole("button", {
        name: "Collapse Knowledge sources",
      }),
    ).toHaveFocus();
    expect(
      within(restoredSources).getByRole("checkbox", {
        name: /life-underwriting-rules\.md/,
      }),
    ).toBeChecked();
  });

  it("builds a question navigation rail with previews and smooth turn jumps", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    renderPrototype();

    const composer = screen.getByRole("textbox", { name: "Message Tapper" });
    await user.type(composer, "First underwriting question");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.type(composer, "Second underwriting question");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const questionNavigation = screen.getByRole("navigation", {
      name: "Questions in this conversation",
    });
    const firstQuestion = within(questionNavigation).getByRole("button", {
      name: "Jump to question 1: First underwriting question",
    });
    const secondQuestion = within(questionNavigation).getByRole("button", {
      name: "Jump to question 2: Second underwriting question",
    });
    expect(questionNavigation).toHaveAttribute("data-placement", "left");
    expect(firstQuestion).not.toHaveAttribute("style");
    expect(secondQuestion).not.toHaveAttribute("style");
    expect(firstQuestion).toHaveAttribute("data-proximity", "rest");
    expect(secondQuestion).toHaveAttribute("data-proximity", "rest");
    expect(secondQuestion).toHaveAttribute("aria-current", "true");

    vi.spyOn(firstQuestion, "getBoundingClientRect").mockReturnValue({
      bottom: 118,
      height: 18,
      left: 12,
      right: 84,
      top: 100,
      width: 72,
      x: 12,
      y: 100,
      toJSON: () => ({}),
    });
    await user.hover(firstQuestion);
    const preview = screen.getByRole("tooltip");
    expect(preview).toHaveTextContent("First underwriting question");
    expect(questionNavigation).not.toContainElement(preview);
    expect(preview).toHaveStyle({ left: "92px", top: "109px" });
    expect(firstQuestion).toHaveAttribute("data-proximity", "focus");
    expect(secondQuestion).toHaveAttribute("data-proximity", "near-1");

    await user.unhover(firstQuestion);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(firstQuestion).toHaveAttribute("data-proximity", "rest");
    expect(secondQuestion).toHaveAttribute("data-proximity", "rest");

    await user.click(firstQuestion);
    expect(scrollIntoView).toHaveBeenCalledWith({
      behavior: "smooth",
      block: "start",
    });
    expect(firstQuestion).toHaveAttribute("aria-current", "true");

    const transcript = screen.getByRole("log", { name: "Conversation" });
    const turns = transcript.querySelectorAll<HTMLElement>(".tap-turn");
    Object.defineProperties(transcript, {
      clientHeight: { configurable: true, value: 400 },
      scrollHeight: { configurable: true, value: 1000 },
      scrollTop: { configurable: true, value: 600 },
    });
    Object.defineProperty(turns[0], "offsetTop", {
      configurable: true,
      value: 100,
    });
    Object.defineProperty(turns[1], "offsetTop", {
      configurable: true,
      value: 900,
    });
    fireEvent.scroll(transcript);
    expect(secondQuestion).toHaveAttribute("aria-current", "true");
    expect(firstQuestion).toHaveAttribute("data-proximity", "rest");
    expect(secondQuestion).toHaveAttribute("data-proximity", "rest");
  });

  it("matches the centered, left-anchored Codex minimap geometry and fisheye", async () => {
    const style = installPrototypeStyles();
    const user = userEvent.setup();

    try {
      renderPrototype();
      const composer = screen.getByRole("textbox", { name: "Message Tapper" });
      for (let index = 1; index <= 7; index += 1) {
        await user.type(composer, `Question ${index}`);
        await user.click(screen.getByRole("button", { name: "Send" }));
      }

      const questionNavigation = screen.getByRole("navigation", {
        name: "Questions in this conversation",
      });
      const questions = within(questionNavigation).getAllByRole("button");
      const markers = questions.map((question) =>
        question.querySelector<HTMLElement>(".tap-question-marker"),
      );
      expect(markers.every((marker) => marker !== null)).toBe(true);

      const navigationStyle = getComputedStyle(questionNavigation);
      expect(navigationStyle.top).toBe("384px");
      expect(navigationStyle.display).toBe("flex");
      expect(navigationStyle.flexDirection).toBe("column");
      expect(navigationStyle.transform).toBe("translateY(-50%)");

      const questionStyle = getComputedStyle(questions[0]!);
      expect(questionStyle.height).toBe("14px");
      expect(questionStyle.justifyContent).toBe("flex-start");
      expect(questionStyle.paddingLeft).toBe("14px");

      const defaultMarkerStyle = getComputedStyle(markers[0]!);
      expect(defaultMarkerStyle.width).toBe("12px");
      expect(defaultMarkerStyle.height).toBe("4px");
      expect(defaultMarkerStyle.backgroundColor).toBe("rgb(219, 219, 219)");

      const activeMarkerStyle = getComputedStyle(markers[6]!);
      expect(activeMarkerStyle.width).toBe("12px");
      expect(activeMarkerStyle.height).toBe("4px");
      expect(activeMarkerStyle.backgroundColor).toBe("rgb(138, 138, 138)");

      await user.hover(questions[3]!);
      expect(questions.map((question) => question.dataset.proximity)).toEqual([
        "near-3",
        "near-2",
        "near-1",
        "focus",
        "near-1",
        "near-2",
        "near-3",
      ]);
      expect(markers.map((marker) => getComputedStyle(marker!).width)).toEqual([
        "14px",
        "18px",
        "24px",
        "34px",
        "24px",
        "18px",
        "14px",
      ]);
      expect(getComputedStyle(markers[3]!).backgroundColor).toBe(
        "rgb(34, 37, 41)",
      );

      await user.unhover(questions[3]!);
      expect(questions.map((question) => question.dataset.proximity)).toEqual(
        Array.from({ length: 7 }, () => "rest"),
      );
      expect(markers.map((marker) => getComputedStyle(marker!).width)).toEqual(
        Array.from({ length: 7 }, () => "12px"),
      );

      expect(getComputedStyle(screen.getByRole("log")).scrollbarWidth).toBe(
        "none",
      );
    } finally {
      style.remove();
    }
  });

  it("keeps a long question minimap inside a viewport-sized window", () => {
    const originalInnerHeight = Object.getOwnPropertyDescriptor(
      window,
      "innerHeight",
    );
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 400,
    });

    try {
      renderPrototypeWithQuestions(30);

      const questionNavigation = screen.getByRole("navigation", {
        name: "Questions in this conversation",
      });
      const visibleQuestions = within(questionNavigation)
        .getAllByRole("button")
        .filter((button) =>
          button.getAttribute("aria-label")?.startsWith("Jump to question"),
        );

      expect(visibleQuestions).toHaveLength(21);
      expect(
        within(questionNavigation).getByRole("button", {
          name: "Jump to question 10: Question 10",
        }),
      ).toBeVisible();
      expect(
        within(questionNavigation).getByRole("button", {
          name: "Jump to question 30: Question 30",
        }),
      ).toHaveAttribute("aria-current", "true");
      expect(
        within(questionNavigation).queryByRole("button", {
          name: "Jump to question 9: Question 9",
        }),
      ).not.toBeInTheDocument();
      expect(
        within(questionNavigation).getByRole("button", {
          name: "Show 9 earlier questions",
        }),
      ).toBeVisible();
      expect(
        within(questionNavigation).queryByRole("button", {
          name: /later questions/,
        }),
      ).not.toBeInTheDocument();
    } finally {
      if (originalInnerHeight === undefined) {
        Reflect.deleteProperty(window, "innerHeight");
      } else {
        Object.defineProperty(window, "innerHeight", originalInnerHeight);
      }
    }
  });

  it("clips the long minimap and fades its continuation without a scroll track", () => {
    const style = installPrototypeStyles();
    const originalInnerHeight = Object.getOwnPropertyDescriptor(
      window,
      "innerHeight",
    );
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 400,
    });

    try {
      renderPrototypeWithQuestions(30);
      const questionNavigation = screen.getByRole("navigation", {
        name: "Questions in this conversation",
      });
      const continuationMarker = within(questionNavigation)
        .getByRole("button", { name: "Show 9 earlier questions" })
        .querySelector<HTMLElement>(".tap-question-overflow-marker");

      expect(getComputedStyle(questionNavigation).maxHeight).toBe("336px");
      expect(getComputedStyle(questionNavigation).overflow).toBe("clip");
      expect(getComputedStyle(questionNavigation).overflowY).not.toBe("auto");
      expect(getComputedStyle(continuationMarker!).width).toBe("12px");
      expect(getComputedStyle(continuationMarker!).maskImage).toContain(
        "linear-gradient",
      );
    } finally {
      style.remove();
      if (originalInnerHeight === undefined) {
        Reflect.deleteProperty(window, "innerHeight");
      } else {
        Object.defineProperty(window, "innerHeight", originalInnerHeight);
      }
    }
  });

  it("browses hidden minimap questions without introducing another scrollbar", async () => {
    const originalInnerHeight = Object.getOwnPropertyDescriptor(
      window,
      "innerHeight",
    );
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 400,
    });

    try {
      const user = userEvent.setup();
      renderPrototypeWithQuestions(46);
      const questionNavigation = screen.getByRole("navigation", {
        name: "Questions in this conversation",
      });

      fireEvent.wheel(questionNavigation, { deltaY: -100 });

      expect(
        within(questionNavigation).getByRole("button", {
          name: "Show 22 earlier questions",
        }),
      ).toBeVisible();
      expect(
        within(questionNavigation).getByRole("button", {
          name: "Show 3 later questions",
        }),
      ).toBeVisible();
      expect(
        within(questionNavigation).queryByRole("button", {
          name: "Jump to question 46: Question 46",
        }),
      ).not.toBeInTheDocument();

      await user.click(
        within(questionNavigation).getByRole("button", {
          name: "Show 22 earlier questions",
        }),
      );
      expect(
        within(questionNavigation).getByRole("button", {
          name: "Jump to question 2: Question 2",
        }),
      ).toBeVisible();
      expect(
        within(questionNavigation).getByRole("button", {
          name: "Show 24 later questions",
        }),
      ).toBeVisible();

      await user.click(
        within(questionNavigation).getByRole("button", {
          name: "Show 1 earlier question",
        }),
      );
      expect(
        within(questionNavigation).getByRole("button", {
          name: "Jump to question 1: Question 1",
        }),
      ).toBeVisible();
      expect(
        within(questionNavigation).queryByRole("button", {
          name: /earlier questions/,
        }),
      ).not.toBeInTheDocument();
    } finally {
      if (originalInnerHeight === undefined) {
        Reflect.deleteProperty(window, "innerHeight");
      } else {
        Object.defineProperty(window, "innerHeight", originalInnerHeight);
      }
    }
  });

  it("recalculates the minimap window when the available height changes", () => {
    const originalInnerHeight = Object.getOwnPropertyDescriptor(
      window,
      "innerHeight",
    );
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 400,
    });

    try {
      renderPrototypeWithQuestions(30);
      const questionNavigation = screen.getByRole("navigation", {
        name: "Questions in this conversation",
      });
      const getVisibleQuestions = () =>
        within(questionNavigation)
          .getAllByRole("button")
          .filter((button) =>
            button.getAttribute("aria-label")?.startsWith("Jump to question"),
          );

      expect(getVisibleQuestions()).toHaveLength(21);

      Object.defineProperty(window, "innerHeight", {
        configurable: true,
        value: 520,
      });
      fireEvent(window, new Event("resize"));

      expect(getVisibleQuestions()).toHaveLength(30);
    } finally {
      if (originalInnerHeight === undefined) {
        Reflect.deleteProperty(window, "innerHeight");
      } else {
        Object.defineProperty(window, "innerHeight", originalInnerHeight);
      }
    }
  });

  it("keeps the minimap above the composer when the transcript is shorter than the viewport", () => {
    const originalInnerHeight = Object.getOwnPropertyDescriptor(
      window,
      "innerHeight",
    );
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 600,
    });

    try {
      renderPrototypeWithQuestions(30);
      const transcript = screen.getByRole("log", { name: "Conversation" });
      Object.defineProperty(transcript, "clientHeight", {
        configurable: true,
        value: 280,
      });

      fireEvent(window, new Event("resize"));

      const questionNavigation = screen.getByRole("navigation", {
        name: "Questions in this conversation",
      });
      const visibleQuestions = within(questionNavigation)
        .getAllByRole("button")
        .filter((button) =>
          button.getAttribute("aria-label")?.startsWith("Jump to question"),
        );

      expect(visibleQuestions).toHaveLength(13);
      expect(questionNavigation).toHaveStyle({
        maxHeight: "216px",
        top: "140px",
      });
    } finally {
      if (originalInnerHeight === undefined) {
        Reflect.deleteProperty(window, "innerHeight");
      } else {
        Object.defineProperty(window, "innerHeight", originalInnerHeight);
      }
    }
  });

  it("moves the minimap window with the active question while the transcript scrolls", () => {
    const originalInnerHeight = Object.getOwnPropertyDescriptor(
      window,
      "innerHeight",
    );
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 400,
    });

    try {
      renderPrototypeWithQuestions(30);
      const transcript = screen.getByRole("log", { name: "Conversation" });
      const turns = transcript.querySelectorAll<HTMLElement>(".tap-turn");
      Object.defineProperties(transcript, {
        clientHeight: { configurable: true, value: 400 },
        scrollHeight: { configurable: true, value: 6000 },
        scrollTop: { configurable: true, value: 800 },
      });
      turns.forEach((turn, index) => {
        Object.defineProperty(turn, "offsetTop", {
          configurable: true,
          value: index * 200,
        });
      });

      fireEvent.scroll(transcript);

      const questionNavigation = screen.getByRole("navigation", {
        name: "Questions in this conversation",
      });
      expect(
        within(questionNavigation).getByRole("button", {
          name: "Jump to question 5: Question 5",
        }),
      ).toHaveAttribute("aria-current", "true");
      expect(
        within(questionNavigation).getByRole("button", {
          name: "Jump to question 1: Question 1",
        }),
      ).toBeVisible();
      expect(
        within(questionNavigation).queryByRole("button", {
          name: "Jump to question 30: Question 30",
        }),
      ).not.toBeInTheDocument();
    } finally {
      if (originalInnerHeight === undefined) {
        Reflect.deleteProperty(window, "innerHeight");
      } else {
        Object.defineProperty(window, "innerHeight", originalInnerHeight);
      }
    }
  });

  it("uses one clip-only motion system for both collapsible panels", () => {
    renderPrototype();

    expect(prototypeStyles).toMatch(/--tap-panel-motion-duration:\s*200ms;/m);
    expect(prototypeStyles).toMatch(
      /--tap-panel-motion-easing:\s*cubic-bezier\(0\.16, 1, 0\.3, 1\);/m,
    );
    expect(prototypeStyles).not.toMatch(
      /\.tap-tapper-sidebar\s*\{[^}]*opacity:/m,
    );
    expect(prototypeStyles).not.toMatch(
      /\.tap-sources-shell\s*\{[^}]*opacity:/m,
    );
  });

  it("filters the Knowledge sources panel by source name", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const sources = screen.getByRole("complementary", {
      name: "Knowledge sources",
    });
    expect(
      await within(sources).findByText("life-underwriting-rules.md"),
    ).toBeVisible();

    await user.type(
      within(sources).getByRole("textbox", {
        name: "Search knowledge sources",
      }),
      "disclosure",
    );

    expect(
      within(sources).getByText("health-disclosure-guide.pdf"),
    ).toBeVisible();
    expect(
      within(sources).queryByText("life-underwriting-rules.md"),
    ).toBeNull();
  });

  it("shows a localized no-match state when source search filters out every ready source", async () => {
    const user = userEvent.setup();
    renderPrototype();
    const sources = screen.getByRole("complementary", {
      name: "Knowledge sources",
    });

    await user.type(
      within(sources).getByRole("textbox", {
        name: "Search knowledge sources",
      }),
      "not-a-source",
    );
    expect(within(sources).getByText("No matching sources")).toBeVisible();
    expect(within(sources).queryByText("No ready sources")).toBeNull();

    await user.click(screen.getByRole("button", { name: "中文" }));
    expect(within(sources).getByText("没有匹配的来源")).toBeVisible();
  });

  it("records selected context separately for each persisted turn", async () => {
    const user = userEvent.setup();
    const { container } = renderPrototype();
    const sources = screen.getByRole("complementary", {
      name: "Knowledge sources",
    });
    const firstPrompt = "What evidence supports this underwriting decision?";

    await user.click(
      await within(sources).findByRole("checkbox", {
        name: /health-disclosure-guide\.pdf/,
      }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      firstPrompt,
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    await user.click(
      within(sources).getByRole("checkbox", {
        name: /health-disclosure-guide\.pdf/,
      }),
    );
    await user.click(
      within(sources).getByRole("checkbox", {
        name: /life-underwriting-rules\.md/,
      }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      "What rules apply to the application?",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    const turns = container.querySelectorAll(".tap-turn");
    expect(turns).toHaveLength(2);
    const firstCitations = within(turns[0] as HTMLElement).getByRole("list", {
      name: "Selected context",
    });
    expect(
      within(firstCitations).getByText("health-disclosure-guide.pdf"),
    ).toBeVisible();
    expect(
      within(firstCitations).queryByText("life-underwriting-rules.md"),
    ).toBeNull();
    const secondCitations = within(turns[1] as HTMLElement).getByRole("list", {
      name: "Selected context",
    });
    expect(
      within(secondCitations).getByText("life-underwriting-rules.md"),
    ).toBeVisible();
    expect(
      within(secondCitations).queryByText("health-disclosure-guide.pdf"),
    ).toBeNull();

    await user.click(screen.getByRole("button", { name: "New chat" }));
    await user.click(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", {
        name: `${firstPrompt} · Conversation 1 · 1 selected`,
      }),
    );
    const restoredTurns = container.querySelectorAll(".tap-turn");
    expect(
      within(restoredTurns[0] as HTMLElement).getByText(
        "health-disclosure-guide.pdf",
      ),
    ).toBeVisible();
    expect(
      within(restoredTurns[1] as HTMLElement).getByText(
        "life-underwriting-rules.md",
      ),
    ).toBeVisible();
  });

  it("renders an explicit no-context notice without fabricated provenance", async () => {
    const user = userEvent.setup();
    const { container } = renderPrototype();

    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      "What information is needed for a life insurance application?",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    const turn = container.querySelector(".tap-turn") as HTMLElement;
    expect(
      within(turn).getByText(/No knowledge context was selected for this turn/),
    ).toBeVisible();
    expect(
      within(turn).queryByRole("list", { name: "Selected context" }),
    ).toBeNull();
    expect(within(turn).queryByText("life-underwriting-rules.md")).toBeNull();
  });

  it("shows the captured context state on generated BDD and automation artifacts", async () => {
    const user = userEvent.setup();
    const { container } = renderPrototype();
    const sources = screen.getByRole("complementary", {
      name: "Knowledge sources",
    });

    await user.click(
      await within(sources).findByRole("checkbox", {
        name: /life-underwriting-rules\.md/,
      }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      "Create BDD tests for beneficiary designation",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    await user.click(
      within(sources).getByRole("checkbox", {
        name: /life-underwriting-rules\.md/,
      }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      "Generate an automation script for policy submission",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    const turns = container.querySelectorAll(".tap-turn");
    expect(
      within(turns[0] as HTMLElement).getByRole("list", {
        name: "Selected context",
      }),
    ).toHaveTextContent("life-underwriting-rules.md");
    expect(
      within(turns[0] as HTMLElement).getByText(
        /records this selection but does not verify document use/,
      ),
    ).toBeVisible();
    expect(
      within(turns[1] as HTMLElement).getByText(
        /No knowledge context was selected for this turn/,
      ),
    ).toBeVisible();
    expect(
      within(turns[1] as HTMLElement).queryByRole("list", {
        name: "Selected context",
      }),
    ).toBeNull();
  });

  it("adds a searchable Library reference to the composer from its plus menu", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Add to message" }));
    const menu = screen.getByRole("menu", { name: "Add to message" });
    expect(
      within(menu).getByRole("menuitem", { name: "Add from Library" }),
    ).toBeVisible();
    expect(
      within(menu).getByRole("menuitem", { name: "Use Agents" }),
    ).toBeVisible();
    expect(
      within(menu).getByRole("menuitem", { name: "Use Skills" }),
    ).toBeVisible();

    await user.click(
      within(menu).getByRole("menuitem", { name: "Add from Library" }),
    );
    const picker = screen.getByRole("dialog", { name: "Add from Library" });
    await user.type(
      within(picker).getByRole("textbox", { name: "Search library" }),
      "disclosure",
    );
    await user.click(
      within(picker).getByRole("option", {
        name: "health-disclosure-guide.pdf",
      }),
    );

    expect(
      within(screen.getByRole("form", { name: "Message composer" })).getByText(
        "health-disclosure-guide.pdf",
      ),
    ).toBeVisible();
  });

  it("adds a searchable Agent to the active conversation context", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Add to message" }));
    const menu = screen.getByRole("menu", { name: "Add to message" });
    await user.click(
      within(menu).getByRole("menuitem", { name: "Use Agents" }),
    );

    const picker = screen.getByRole("dialog", { name: "Use Agents" });
    await user.type(
      within(picker).getByRole("textbox", { name: "Search agents" }),
      "underwriting",
    );
    await user.click(
      within(picker).getByRole("option", {
        name: "Life Underwriting Analyst",
      }),
    );

    expect(
      within(screen.getByRole("form", { name: "Message composer" })).getByText(
        "Life Underwriting Analyst",
      ),
    ).toBeVisible();
  });

  it("adds a searchable Skill to the active conversation context", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Add to message" }));
    const menu = screen.getByRole("menu", { name: "Add to message" });
    await user.click(
      within(menu).getByRole("menuitem", { name: "Use Skills" }),
    );

    const picker = screen.getByRole("dialog", { name: "Use Skills" });
    await user.type(
      within(picker).getByRole("textbox", { name: "Search skills" }),
      "scenario",
    );
    await user.click(
      within(picker).getByRole("option", { name: "BDD Scenario Design" }),
    );

    expect(
      within(screen.getByRole("form", { name: "Message composer" })).getByText(
        "BDD Scenario Design",
      ),
    ).toBeVisible();
  });

  it("supports keyboard navigation and Escape focus restoration in the add menu", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const trigger = screen.getByRole("button", { name: "Add to message" });
    trigger.focus();
    await user.keyboard("{Enter}");

    const menu = screen.getByRole("menu", { name: "Add to message" });
    const libraryItem = within(menu).getByRole("menuitem", {
      name: "Add from Library",
    });
    const agentItem = within(menu).getByRole("menuitem", {
      name: "Use Agents",
    });
    const skillItem = within(menu).getByRole("menuitem", {
      name: "Use Skills",
    });
    expect(libraryItem).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(agentItem).toHaveFocus();
    await user.keyboard("{End}");
    expect(skillItem).toHaveFocus();
    await user.keyboard("{Home}");
    expect(libraryItem).toHaveFocus();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("menu", { name: "Add to message" })).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("contains dialog focus and restores it to the add trigger on Escape", async () => {
    const user = userEvent.setup();
    const { container } = renderPrototype();

    const trigger = screen.getByRole("button", { name: "Add to message" });
    await user.click(trigger);
    await user.keyboard("{ArrowDown}{Enter}");

    const picker = screen.getByRole("dialog", { name: "Use Agents" });
    expect(within(picker).getByRole("listbox")).toHaveAttribute(
      "aria-multiselectable",
      "true",
    );
    const search = within(picker).getByRole("textbox", {
      name: "Search agents",
    });
    const close = within(picker).getByRole("button", {
      name: "Close Use Agents",
    });
    const lastOption = within(picker).getByRole("option", {
      name: "Application Completeness Reviewer",
    });
    expect(search).toHaveFocus();
    expect(container.querySelector(".tap-product-shell")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(screen.queryByRole("navigation", { name: "Product" })).toBeNull();

    await user.tab({ shift: true });
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(lastOption).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Use Agents" })).toBeNull();
    expect(trigger).toHaveFocus();
    expect(container.querySelector(".tap-product-shell")).not.toHaveAttribute(
      "aria-hidden",
    );
  });

  it("reports selected composer context through option aria-selected", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const trigger = screen.getByRole("button", { name: "Add to message" });
    await user.click(trigger);
    await user.click(
      within(screen.getByRole("menu", { name: "Add to message" })).getByRole(
        "menuitem",
        { name: "Use Agents" },
      ),
    );
    await user.click(
      screen.getByRole("option", { name: "Life Underwriting Analyst" }),
    );

    await user.click(trigger);
    await user.click(
      within(screen.getByRole("menu", { name: "Add to message" })).getByRole(
        "menuitem",
        { name: "Use Agents" },
      ),
    );

    expect(
      screen.getByRole("option", { name: "Life Underwriting Analyst" }),
    ).toHaveAttribute("aria-selected", "true");
  });

  it("searches, creates, and edits agents for the life-underwriting workflow", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Agent" }));
    expect(screen.getByRole("heading", { name: "Agents" })).toBeVisible();
    await user.type(
      screen.getByRole("textbox", { name: "Search agents" }),
      "underwriting",
    );
    await user.click(screen.getByRole("button", { name: "Create agent" }));

    const createDialog = screen.getByRole("dialog", { name: "Create agent" });
    expect(
      within(createDialog).getByRole("button", { name: "Save agent" }),
    ).toBeDisabled();
    await user.type(
      within(createDialog).getByRole("textbox", { name: "Name" }),
      "Life underwriting reviewer",
    );
    await user.type(
      within(createDialog).getByRole("textbox", { name: "Instructions" }),
      "Review the selected evidence before escalating an application.",
    );
    await user.click(
      within(createDialog).getByRole("button", { name: "Save agent" }),
    );
    const customAgent = screen.getByRole("listitem", {
      name: "Life underwriting reviewer",
    });
    expect(
      within(customAgent).getByRole("heading", {
        name: "Life underwriting reviewer",
      }),
    ).toBeVisible();
    expect(within(customAgent).getByText("Custom")).toBeVisible();
    expect(
      within(customAgent).getByText(
        "Review the selected evidence before escalating an application.",
      ),
    ).toBeVisible();
    expect(
      within(
        screen.getByRole("listitem", { name: "Life Underwriting Analyst" }),
      ).getByText("Built-in"),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: "Edit Life underwriting reviewer" }),
    );
    const editDialog = screen.getByRole("dialog", { name: "Edit agent" });
    const description = within(editDialog).getByRole("textbox", {
      name: "Description",
    });
    await user.type(description, "Escalates high-risk life applications.");
    await user.click(
      within(editDialog).getByRole("button", { name: "Save agent" }),
    );
    expect(
      screen.getByText("Escalates high-risk life applications."),
    ).toBeVisible();
    expect(
      screen.getByText(
        "Review the selected evidence before escalating an application.",
      ),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", {
        name: "Use Life underwriting reviewer in chat",
      }),
    );
    expect(
      within(screen.getByRole("form", { name: "Message composer" })).getByText(
        "Life underwriting reviewer",
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Add to message" }));
    await user.click(
      within(screen.getByRole("menu", { name: "Add to message" })).getByRole(
        "menuitem",
        { name: "Use Agents" },
      ),
    );
    expect(
      screen.getByRole("option", { name: "Life underwriting reviewer" }),
    ).toHaveAttribute("aria-selected", "true");
  });

  it("localizes catalog list labels instead of composing English aria text", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "中文" }));
    await user.click(screen.getByRole("button", { name: "智能体" }));
    expect(screen.getByRole("list", { name: "智能体目录" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "技能" }));
    expect(screen.getByRole("list", { name: "技能目录" })).toBeVisible();
  });

  it("contains create-dialog focus, hides the product background, and restores the Create trigger", async () => {
    const user = userEvent.setup();
    const { container } = renderPrototype();

    await user.click(screen.getByRole("button", { name: "Agent" }));
    const trigger = screen.getByRole("button", { name: "Create agent" });
    await user.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Create agent" });
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    const cancel = within(dialog).getByRole("button", { name: "Cancel" });
    expect(name).toHaveFocus();
    expect(container.querySelector(".tap-product-shell")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(screen.queryByRole("navigation", { name: "Product" })).toBeNull();

    await user.tab({ shift: true });
    expect(cancel).toHaveFocus();
    await user.tab();
    expect(name).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Create agent" })).toBeNull();
    expect(trigger).toHaveFocus();
    expect(container.querySelector(".tap-product-shell")).not.toHaveAttribute(
      "aria-hidden",
    );
  });

  it("contains edit-dialog focus and restores the exact Edit trigger", async () => {
    const user = userEvent.setup();
    const { container } = renderPrototype();

    await user.click(screen.getByRole("button", { name: "Agent" }));
    const trigger = screen.getByRole("button", {
      name: "Edit Life Underwriting Analyst",
    });
    await user.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Edit agent" });
    const name = within(dialog).getByRole("textbox", { name: "Name" });
    const save = within(dialog).getByRole("button", { name: "Save agent" });
    expect(name).toHaveFocus();
    expect(container.querySelector(".tap-product-shell")).toHaveAttribute(
      "aria-hidden",
      "true",
    );

    await user.tab({ shift: true });
    expect(save).toHaveFocus();
    await user.tab();
    expect(name).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Edit agent" })).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("searches, creates, and edits reusable underwriting skills", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Skills" }));
    expect(screen.getByRole("heading", { name: "Skills" })).toBeVisible();
    await user.type(
      screen.getByRole("textbox", { name: "Search skills" }),
      "underwriting",
    );
    await user.click(screen.getByRole("button", { name: "Create skill" }));

    const createDialog = screen.getByRole("dialog", { name: "Create skill" });
    expect(
      within(createDialog).getByRole("button", { name: "Save skill" }),
    ).toBeDisabled();
    await user.type(
      within(createDialog).getByRole("textbox", { name: "Name" }),
      "Underwriting rules lookup",
    );
    await user.type(
      within(createDialog).getByRole("textbox", { name: "Instructions" }),
      "Find relevant rules and cite the selected source.",
    );
    await user.click(
      within(createDialog).getByRole("button", { name: "Save skill" }),
    );
    const customSkill = screen.getByRole("listitem", {
      name: "Underwriting rules lookup",
    });
    expect(
      within(customSkill).getByRole("heading", {
        name: "Underwriting rules lookup",
      }),
    ).toBeVisible();
    expect(within(customSkill).getByText("Custom")).toBeVisible();
    expect(
      within(customSkill).getByText(
        "Find relevant rules and cite the selected source.",
      ),
    ).toBeVisible();
    expect(
      within(
        screen.getByRole("listitem", { name: "BDD Scenario Design" }),
      ).getByText("Built-in"),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: "Edit Underwriting rules lookup" }),
    );
    const editDialog = screen.getByRole("dialog", { name: "Edit skill" });
    const description = within(editDialog).getByRole("textbox", {
      name: "Description",
    });
    await user.type(description, "Retrieves rule evidence before a decision.");
    await user.click(
      within(editDialog).getByRole("button", { name: "Save skill" }),
    );
    expect(
      screen.getByText("Retrieves rule evidence before a decision."),
    ).toBeVisible();
    expect(
      screen.getByText("Find relevant rules and cite the selected source."),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", {
        name: "Use Underwriting rules lookup in chat",
      }),
    );
    expect(
      within(screen.getByRole("form", { name: "Message composer" })).getByText(
        "Underwriting rules lookup",
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Add to message" }));
    await user.click(
      within(screen.getByRole("menu", { name: "Add to message" })).getByRole(
        "menuitem",
        { name: "Use Skills" },
      ),
    );
    expect(
      screen.getByRole("option", { name: "Underwriting rules lookup" }),
    ).toHaveAttribute("aria-selected", "true");
  });

  it("adds a local Library file to the shared source controls", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(screen.getByRole("heading", { name: "Library" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Add source" }));
    const addDialog = screen.getByRole("dialog", { name: "Add source" });
    const localFile = new File(
      ["beneficiary guidance"],
      "beneficiary-guide.txt",
      {
        type: "text/plain",
      },
    );
    await user.upload(
      within(addDialog).getByLabelText("Source file"),
      localFile,
    );
    await user.click(
      within(addDialog).getByRole("button", { name: "Add source" }),
    );

    expect(
      within(screen.getByRole("list", { name: "Library sources" })).getByText(
        "beneficiary-guide.txt",
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(
      within(
        screen.getByRole("complementary", { name: "Knowledge sources" }),
      ).getByText("beneficiary-guide.txt"),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Add to message" }));
    await user.click(
      within(screen.getByRole("menu", { name: "Add to message" })).getByRole(
        "menuitem",
        { name: "Add from Library" },
      ),
    );
    expect(
      screen.getByRole("option", { name: "beneficiary-guide.txt" }),
    ).toBeVisible();
  });

  it("identifies and cites a page-local Library source without an immutable label", async () => {
    const user = userEvent.setup();
    const { container } = renderPrototype();

    await user.click(screen.getByRole("button", { name: "Library" }));
    await user.click(screen.getByRole("button", { name: "Add source" }));
    const addDialog = screen.getByRole("dialog", { name: "Add source" });
    await user.upload(
      within(addDialog).getByLabelText("Source file"),
      new File(["beneficiary guidance"], "beneficiary-guide.txt", {
        type: "text/plain",
      }),
    );
    await user.click(
      within(addDialog).getByRole("button", { name: "Add source" }),
    );
    await user.click(screen.getByRole("button", { name: "New chat" }));

    const sources = screen.getByRole("complementary", {
      name: "Knowledge sources",
    });
    const localSource = within(sources).getByRole("checkbox", {
      name: /beneficiary-guide\.txt/,
    });
    expect(localSource).toHaveAccessibleName(
      "beneficiary-guide.txtReady · Page-local Library source",
    );
    expect(localSource).not.toHaveAccessibleName(/immutable revision/);
    await user.click(localSource);
    await user.type(
      screen.getByRole("textbox", { name: "Message Tapper" }),
      "What beneficiary details are needed?",
    );
    await user.click(screen.getByRole("button", { name: "Send" }));

    const citations = within(
      container.querySelector(".tap-turn") as HTMLElement,
    ).getByRole("list", { name: "Selected context" });
    expect(within(citations).getByText("beneficiary-guide.txt")).toBeVisible();
    expect(
      within(citations).getByText("Page-local Library source"),
    ).toBeVisible();
  });

  it("contains add-source focus, hides the product background, and restores its trigger", async () => {
    const user = userEvent.setup();
    const { container } = renderPrototype();

    await user.click(screen.getByRole("button", { name: "Library" }));
    const trigger = screen.getByRole("button", { name: "Add source" });
    await user.click(trigger);

    const dialog = screen.getByRole("dialog", { name: "Add source" });
    const file = within(dialog).getByLabelText("Source file");
    const cancel = within(dialog).getByRole("button", { name: "Cancel" });
    expect(file).toHaveFocus();
    expect(container.querySelector(".tap-product-shell")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    expect(screen.queryByRole("navigation", { name: "Product" })).toBeNull();

    await user.tab({ shift: true });
    expect(cancel).toHaveFocus();
    await user.tab();
    expect(file).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Add source" })).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("keeps a locally added source description in the current interface language", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Library" }));
    await user.click(screen.getByRole("button", { name: "Add source" }));
    const dialog = screen.getByRole("dialog", { name: "Add source" });
    await user.upload(
      within(dialog).getByLabelText("Source file"),
      new File(["beneficiary guidance"], "beneficiary-guide.txt", {
        type: "text/plain",
      }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Add source" }),
    );
    expect(screen.getByText("Local source · page-only")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "中文" }));
    expect(screen.getByText("本地来源 · 仅当前页面")).toBeVisible();
    expect(screen.queryByText("Local source · page-only")).toBeNull();
  });

  it("switches the Library between All sources and an interactive Knowledge Graph", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(
      screen.getByRole("tab", { name: "All", selected: true }),
    ).toBeVisible();
    const search = screen.getByRole("textbox", { name: "Search library" });
    await user.type(search, "disclosure");
    const filteredSources = screen.getByRole("list", {
      name: "Library sources",
    });
    expect(
      within(filteredSources).getByText("health-disclosure-guide.pdf"),
    ).toBeVisible();
    expect(
      within(filteredSources).queryByText("life-underwriting-rules.md"),
    ).toBeNull();

    await user.click(screen.getByRole("tab", { name: "Knowledge Graph" }));
    expect(
      screen.getByRole("tab", { name: "Knowledge Graph", selected: true }),
    ).toBeVisible();
    const graph = screen.getByRole("group", {
      name: "Life insurance knowledge graph",
    });
    expect(graph).toBeVisible();
    expect(within(graph).getByText(/health-disclosure-guide/)).toBeVisible();
    expect(
      within(graph).getByRole("button", {
        name: /health-disclosure-guide\.pdf/,
      }),
    ).toHaveAttribute("data-highlighted", "true");
    expect(
      within(graph).getByRole("button", {
        name: /life-underwriting-rules\.md/,
      }),
    ).toHaveAttribute("data-dimmed", "true");
    expect(screen.getByText(/Illustrative view/)).toBeVisible();
    expect(within(graph).getByText("Health disclosure")).toBeVisible();
    expect(within(graph).getByText("informs")).toBeVisible();

    await user.clear(search);
    await user.click(screen.getByRole("tab", { name: "All" }));
    expect(screen.getByRole("list", { name: "Library sources" })).toBeVisible();
  });

  it("combines Library type and status filters and clears them together", async () => {
    const user = userEvent.setup();
    renderPrototypeWithLibraryStatuses();

    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(screen.getByText("4/4 sources")).toBeVisible();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Type" }),
      "PDF",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Status" }),
      "failed",
    );

    const filteredSources = screen.getByRole("list", {
      name: "Library sources",
    });
    expect(
      within(filteredSources).getByText("health-disclosure-guide.pdf"),
    ).toBeVisible();
    expect(
      within(filteredSources).queryByText("life-underwriting-rules.md"),
    ).toBeNull();
    expect(screen.getByText("1/4 sources")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByText("4/4 sources")).toBeVisible();
    expect(
      within(screen.getByRole("list", { name: "Library sources" })).getByText(
        "application-checklist.docx",
      ),
    ).toBeVisible();
  });

  it("filters graph communities, inspects nodes, and controls the viewport", async () => {
    const user = userEvent.setup();
    renderPrototypeWithManyDocuments();

    await user.click(screen.getByRole("button", { name: "Library" }));
    await user.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

    const graph = screen.getByRole("group", {
      name: "Life insurance knowledge graph",
    });
    const sourcesCommunity = screen.getByRole("checkbox", {
      name: /Sources · 5 nodes/,
    });
    expect(sourcesCommunity).toBeChecked();
    await user.click(sourcesCommunity);
    expect(
      within(graph).queryByRole("button", { name: /beneficiary-guide\.md/ }),
    ).toBeNull();
    await user.click(sourcesCommunity);

    await user.click(
      within(graph).getByRole("button", {
        name: "Health disclosure · Concept · Underwriting",
      }),
    );
    const inspector = screen.getByRole("region", { name: "Node details" });
    expect(within(inspector).getByText("Health disclosure")).toBeVisible();
    expect(within(inspector).getByText("3 connections")).toBeVisible();
    expect(within(inspector).getByText("EXTRACTED")).toBeVisible();

    expect(
      screen.getByRole("status", { name: "Zoom level" }),
    ).toHaveTextContent("100%");
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(
      screen.getByRole("status", { name: "Zoom level" }),
    ).toHaveTextContent("125%");
    await user.click(screen.getByRole("button", { name: "Reset view" }));
    expect(
      screen.getByRole("status", { name: "Zoom level" }),
    ).toHaveTextContent("100%");
    const zoomOut = screen.getByRole("button", { name: "Zoom out" });
    await user.click(zoomOut);
    await user.click(zoomOut);
    expect(
      screen.getByRole("status", { name: "Zoom level" }),
    ).toHaveTextContent("50%");
    expect(zoomOut).toBeDisabled();
  });

  it("summarizes every visible graph document, concept, and labeled relationship", async () => {
    const user = userEvent.setup();
    renderPrototypeWithManyDocuments();

    await user.click(screen.getByRole("button", { name: "Library" }));
    await user.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

    const graph = screen.getByRole("group", {
      name: "Life insurance knowledge graph",
    });
    const summary = screen.getByRole("region", {
      name: "Knowledge graph summary",
    });
    const documents = within(summary).getByRole("list", {
      name: "Visible documents",
    });
    expect(within(documents).getAllByRole("listitem")).toHaveLength(5);
    expect(within(documents).getByText("beneficiary-guide.md")).toBeVisible();
    expect(within(graph).getByText("beneficiary-guide.md")).toBeVisible();

    const concepts = within(summary).getByRole("list", {
      name: "Concepts",
    });
    expect(
      within(concepts)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      "Life insurance application",
      "Underwriting",
      "Health disclosure",
      "Beneficiary",
    ]);

    const relationships = within(summary).getByRole("list", {
      name: "Labeled relationships",
    });
    expect(
      within(relationships).getByText(
        "Life insurance application requires Health disclosure",
      ),
    ).toBeInTheDocument();
    expect(
      within(relationships).getByText("Health disclosure informs Underwriting"),
    ).toBeInTheDocument();
    expect(
      within(relationships).getByText(
        "Life insurance application names Beneficiary",
      ),
    ).toBeInTheDocument();
    expect(
      within(relationships).getByText(
        "beneficiary-guide.md supports Life insurance application",
      ),
    ).toBeInTheDocument();
    expect(graph).toHaveAttribute(
      "aria-describedby",
      expect.stringContaining("tap-library-graph-summary"),
    );
    expect(within(graph).queryByText("寿险投保")).toBeNull();
    expect(within(graph).queryByText("健康告知")).toBeNull();
    expect(within(graph).queryByText("核保")).toBeNull();
    expect(within(graph).queryByText("受益人")).toBeNull();
  });
});
