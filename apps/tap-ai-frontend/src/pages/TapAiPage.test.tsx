import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { fakeKnowledgeClient } from "../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../features/knowledge/testing/renderKnowledgeApp";
import { TapAiPage } from "./TapAiPage";

it("shows only TAP AI product modules", () => {
  renderKnowledgeApp(<TapAiPage conversationSource="fixture" />, {
    api: fakeKnowledgeClient(),
  });
  const product = screen.getByRole("navigation", { name: "Product" });
  expect(screen.getByRole("img", { name: "TAP AI" })).toBeVisible();
  expect(
    within(product)
      .getAllByRole("button")
      .map((button) => button.getAttribute("aria-label")),
  ).toEqual(["Tapper", "Test Management"]);
});

it("does not expose the legacy TAP automation and analytics workspace", async () => {
  renderKnowledgeApp(<TapAiPage conversationSource="fixture" />, {
    api: fakeKnowledgeClient(),
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Test Management" }),
  );
  expect(screen.queryByText("TP-101")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Low Code Automation" }),
  ).not.toBeInTheDocument();
});

it("does not offer the legacy automation workflow in TAP AI chat", async () => {
  renderKnowledgeApp(<TapAiPage conversationSource="fixture" />, {
    api: fakeKnowledgeClient(),
  });
  const user = userEvent.setup();
  await user.type(
    screen.getByRole("textbox", { name: "Message Tapper" }),
    "Generate automation script for login",
  );
  await user.click(screen.getByRole("button", { name: "Send" }));
  expect(
    screen.queryByRole("button", { name: "Create Test Plan first" }),
  ).not.toBeInTheDocument();
});
