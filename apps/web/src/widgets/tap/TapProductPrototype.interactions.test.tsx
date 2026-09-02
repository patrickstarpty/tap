import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  document,
  fakeKnowledgeClient,
} from "../../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../../features/knowledge/testing/renderKnowledgeApp";
import { TapProductPrototype } from "./TapProductPrototype";

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

describe("Tap product prototype interactions", () => {
  it("defaults to English and lets the user switch the interface language", async () => {
    const user = userEvent.setup();
    renderPrototype();

    expect(
      screen.getByRole("button", { name: "English", pressed: true }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "What can I do for you?" }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "中文" }));

    expect(
      screen.getByRole("button", { name: "中文", pressed: true }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "我能为您做什么？" }),
    ).toBeVisible();
  });

  it("fills and focuses the composer when a suggested underwriting prompt is chosen", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const composer = screen.getByRole("textbox", { name: "Message Athena" });
    const prompt = "Summarize the life insurance underwriting rules";
    await user.click(screen.getByRole("button", { name: prompt }));

    expect(composer).toHaveValue(prompt);
    expect(composer).toHaveFocus();
    expect(screen.queryByRole("log", { name: "Conversation" })).toBeNull();
  });

  it("shows the integrated product sidebar and can collapse and expand it", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const navigation = screen.getByRole("navigation", { name: "Product" });
    expect(
      within(navigation)
        .getAllByRole("button")
        .map((item) => item.textContent?.trim()),
    ).toEqual([
      "Athena",
      "New Chat",
      "Agent",
      "Skills",
      "Library",
      "Test Management",
      "Low Code Automation",
    ]);

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(navigation).toHaveAttribute("data-collapsed", "true");

    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(navigation).toHaveAttribute("data-collapsed", "false");
  });

  it("starts a new empty chat while preserving and restoring earlier life-underwriting chats", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const message = "What evidence is needed for life insurance underwriting?";
    await user.type(
      screen.getByRole("textbox", { name: "Message Athena" }),
      message,
    );
    await user.keyboard("{Enter}");
    expect(screen.getByText(message)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "New Chat" }));
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

    await user.click(screen.getByRole("button", { name: "New Chat" }));

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
        name: "New Chat · Conversation 1 · 3 selected",
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
    renderPrototype();

    const trigger = screen.getByRole("button", { name: "Add to message" });
    await user.click(trigger);
    await user.keyboard("{ArrowDown}{Enter}");

    const picker = screen.getByRole("dialog", { name: "Use Agents" });
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

    await user.tab({ shift: true });
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(lastOption).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Use Agents" })).toBeNull();
    expect(trigger).toHaveFocus();
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
    await user.click(screen.getByRole("button", { name: "Athena" }));
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

  it("switches the Library between searchable thumbnails and an illustrative Knowledge Graph", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(
      screen.getByRole("tab", { name: "Thumbnail list", selected: true }),
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
    const graph = screen.getByRole("img", {
      name: "Life insurance knowledge graph",
    });
    expect(graph).toBeVisible();
    expect(within(graph).getByText(/health-disclosure-guide/)).toBeVisible();
    expect(within(graph).queryByText(/life-underwriting-rules/)).toBeNull();
    expect(screen.getByText(/Illustrative view/)).toBeVisible();
    expect(within(graph).getByText("Health disclosure")).toBeVisible();
    expect(within(graph).getByText("informs")).toBeVisible();

    await user.clear(search);
    await user.click(screen.getByRole("tab", { name: "Thumbnail list" }));
    expect(screen.getByRole("list", { name: "Library sources" })).toBeVisible();
  });

  it("summarizes every visible graph document, concept, and labeled relationship", async () => {
    const user = userEvent.setup();
    renderPrototypeWithManyDocuments();

    await user.click(screen.getByRole("button", { name: "Library" }));
    await user.click(screen.getByRole("tab", { name: "Knowledge Graph" }));

    const graph = screen.getByRole("img", {
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
