import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { fakeKnowledgeClient } from "../../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../../features/knowledge/testing/renderKnowledgeApp";
import { TapProductPrototype } from "./TapProductPrototype";
import { loadPrototypeSnapshot } from "./prototype/artifacts/persistence";

function renderPrototype() {
  return renderKnowledgeApp(<TapProductPrototype />, {
    api: fakeKnowledgeClient().withDocuments([]),
  });
}

describe("Tapper floating assistant", () => {
  beforeEach(() => window.localStorage.clear());

  it("appears only outside the Tapper workspace", async () => {
    const user = userEvent.setup();
    renderPrototype();
    expect(
      screen.queryByRole("button", { name: "Ask Tapper" }),
    ).not.toBeInTheDocument();
    for (const module of ["Agents", "Skills", "Library"]) {
      await user.click(screen.getByRole("button", { name: module }));
      expect(
        screen.queryByRole("button", { name: "Ask Tapper" }),
      ).not.toBeInTheDocument();
    }
    for (const module of ["Test Management", "Low Code Automation"]) {
      await user.click(screen.getByRole("button", { name: module }));
      const launcher = screen.getByRole("button", { name: "Ask Tapper" });
      expect(launcher).toHaveAttribute("aria-expanded", "false");
      expect(launcher.querySelector("img")).toHaveAttribute(
        "src",
        expect.stringContaining("tapper-listening-launcher-light.svg"),
      );
    }
  });

  it("opens a nonmodal panel, preserves the draft, and restores focus on Escape", async () => {
    const user = userEvent.setup();
    renderPrototype();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    const launcher = screen.getByRole("button", { name: "Ask Tapper" });
    await user.click(launcher);
    const panel = screen.getByRole("dialog", { name: "Tapper assistant" });
    expect(panel).not.toHaveAttribute("aria-modal", "true");
    const composer = within(panel).getByRole("textbox", {
      name: "Message Tapper",
    });
    expect(composer).toHaveFocus();
    await user.type(composer, "What should I explore next?");
    await user.keyboard("{Escape}");
    expect(
      screen.queryByRole("dialog", { name: "Tapper assistant" }),
    ).not.toBeInTheDocument();
    expect(launcher).toHaveFocus();
    await user.click(launcher);
    expect(screen.getByRole("textbox", { name: "Message Tapper" })).toHaveValue(
      "What should I explore next?",
    );
    const ids = [...window.document.querySelectorAll("[id]")].map(
      (element) => element.id,
    );
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("updates visible page context without discarding a question", async () => {
    const user = userEvent.setup();
    renderPrototype();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Ask Tapper" }));
    let panel = screen.getByRole("dialog", { name: "Tapper assistant" });
    await user.type(
      within(panel).getByRole("textbox", { name: "Message Tapper" }),
      "Check this",
    );
    await user.click(screen.getByRole("button", { name: "Open TP-101" }));
    panel = screen.getByRole("dialog", { name: "Tapper assistant" });
    expect(within(panel).getByText(/^TP-101 ·/)).toBeVisible();
    expect(
      within(panel).getByRole("textbox", { name: "Message Tapper" }),
    ).toHaveValue("Check this");
  });

  it("does not submit an IME composition or a blank question", async () => {
    const user = userEvent.setup();
    renderPrototype();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Ask Tapper" }));
    const panel = screen.getByRole("dialog", { name: "Tapper assistant" });
    expect(
      within(panel).getByRole("button", { name: "Send message" }),
    ).toBeDisabled();
    const composer = within(panel).getByRole("textbox", {
      name: "Message Tapper",
    });
    fireEvent.change(composer, { target: { value: "探索边界" } });
    fireEvent.keyDown(composer, {
      key: "Enter",
      isComposing: true,
      keyCode: 229,
    });
    expect(composer).toHaveValue("探索边界");
    expect(within(panel).queryByRole("log")).not.toBeInTheDocument();
  });

  it("shares replies, the unsent draft, and page context with the full Tapper workspace", async () => {
    const user = userEvent.setup();
    const app = renderPrototype();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Open TP-101" }));
    await user.click(screen.getByRole("button", { name: "Ask Tapper" }));
    const panel = screen.getByRole("dialog", { name: "Tapper assistant" });
    const composer = within(panel).getByRole("textbox", {
      name: "Message Tapper",
    });
    await user.type(composer, "What should I explore next?{Enter}");
    expect(
      await within(panel).findByText(
        "Prototype suggestion · based on page data",
      ),
    ).toBeInTheDocument();
    await user.type(composer, "And what about the boundaries?");
    await user.click(
      within(panel).getByRole("button", { name: "Continue in Tapper" }),
    );
    expect(
      screen.queryByRole("button", { name: "Ask Tapper" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message Tapper" })).toHaveValue(
      "And what about the boundaries?",
    );
    expect(
      screen.getByRole("textbox", { name: "Message Tapper" }),
    ).toHaveFocus();
    expect(
      screen.getByText("What should I explore next?", {
        selector: ".tap-user-message",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Remove.*TP-101/ }),
    ).toBeInTheDocument();
    // The completed contextual turn is part of the existing persisted conversation.
    app.unmount();
    renderPrototype();
    expect(
      screen.getByText("What should I explore next?", {
        selector: ".tap-user-message",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Prototype suggestion · based on page data"),
    ).toBeInTheDocument();
  });

  it("uses Aha only for an unread reply and returns to Listening after it is viewed", async () => {
    const user = userEvent.setup();
    renderPrototype();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Ask Tapper" }));
    const panel = screen.getByRole("dialog", { name: "Tapper assistant" });
    await user.type(
      within(panel).getByRole("textbox", { name: "Message Tapper" }),
      "Explain this page{Enter}",
    );
    await user.click(
      within(panel).getByRole("button", { name: "Minimize Tapper" }),
    );
    const unread = await screen.findByRole("button", {
      name: "Tapper has a new reply",
    });
    expect(unread.querySelector("img")).toHaveAttribute(
      "src",
      expect.stringContaining("tapper-aha-launcher-light.svg"),
    );
    await user.click(unread);
    await user.click(screen.getByRole("button", { name: "Minimize Tapper" }));
    expect(
      screen.getByRole("button", { name: "Ask Tapper" }).querySelector("img"),
    ).toHaveAttribute(
      "src",
      expect.stringContaining("tapper-listening-launcher-light.svg"),
    );
  });

  it("keeps the submitted page snapshot when navigating before the reply is ready", async () => {
    const user = userEvent.setup();
    renderPrototype();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Open TP-101" }));
    await user.click(screen.getByRole("button", { name: "Ask Tapper" }));
    const panel = screen.getByRole("dialog", { name: "Tapper assistant" });
    await user.click(
      within(panel).getByRole("button", {
        name: "Explain this page",
      }),
    );
    expect(
      within(panel).getByRole("textbox", { name: "Message Tapper" }),
    ).toHaveValue("Explain this page");
    expect(within(panel).queryByRole("log")).not.toBeInTheDocument();
    await user.click(
      within(panel).getByRole("button", { name: "Send message" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Low Code Automation" }),
    );
    expect(
      await within(panel).findByText(
        "Prototype suggestion · based on page data",
      ),
    ).toBeInTheDocument();
    expect(within(panel).getByText("Automation Library")).toBeVisible();
    await user.click(within(panel).getByText("Page information at send time"));
    expect(within(panel).getByText(/^TP-101 ·/)).toBeVisible();
  });

  it("preserves submission order when handing off before the floating reply appears", async () => {
    const user = userEvent.setup();
    renderPrototype();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Ask Tapper" }));
    const panel = screen.getByRole("dialog", { name: "Tapper assistant" });
    fireEvent.change(
      within(panel).getByRole("textbox", { name: "Message Tapper" }),
      { target: { value: "Explain this page" } },
    );
    await user.click(
      within(panel).getByRole("button", { name: "Send message" }),
    );
    await user.click(
      within(panel).getByRole("button", { name: "Continue in Tapper" }),
    );
    const fullComposer = screen.getByRole("textbox", {
      name: "Message Tapper",
    });
    fireEvent.change(fullComposer, {
      target: { value: "Review coverage gaps" },
    });
    fireEvent.keyDown(fullComposer, { key: "Enter" });
    await waitFor(() =>
      expect(
        loadPrototypeSnapshot(window.localStorage)?.conversations[0]?.turns,
      ).toHaveLength(2),
    );
    const conversation = loadPrototypeSnapshot(window.localStorage)!
      .conversations[0]!;
    expect(conversation.turns.map((turn) => turn.prompt)).toEqual([
      "Explain this page",
      "Review coverage gaps",
    ]);
    expect(conversation.title).toBe("Explain this page");
  });

  it("does not announce a reply again after it was viewed in full Tapper", async () => {
    const user = userEvent.setup();
    renderPrototype();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Ask Tapper" }));
    const panel = screen.getByRole("dialog", { name: "Tapper assistant" });
    fireEvent.change(
      within(panel).getByRole("textbox", { name: "Message Tapper" }),
      { target: { value: "Explain this page" } },
    );
    await user.click(
      within(panel).getByRole("button", { name: "Send message" }),
    );
    await user.click(
      within(panel).getByRole("button", { name: "Continue in Tapper" }),
    );
    expect(
      screen.getByText("Prototype suggestion · based on page data"),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 650));
    });
    expect(
      screen.queryByRole("button", { name: "Tapper has a new reply" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Ask Tapper" }),
    ).toBeInTheDocument();
  });
});
