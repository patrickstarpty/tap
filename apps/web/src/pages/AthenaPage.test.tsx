import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  document,
  fakeKnowledgeClient,
} from "../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../features/knowledge/testing/renderKnowledgeApp";
import { AthenaPage } from "./AthenaPage";

function renderPrototype() {
  const api = fakeKnowledgeClient().withDocuments([
    document({
      documentId: "life-underwriting-rules",
      filename: "life-underwriting-rules.md",
      status: "ready",
      stage: "ready",
    }),
    document({
      documentId: "health-disclosure-guide",
      filename: "health-disclosure-guide.pdf",
      status: "ready",
      stage: "ready",
    }),
  ]);
  return renderKnowledgeApp(<AthenaPage />, { api });
}

async function sendMessage(
  user: ReturnType<typeof userEvent.setup>,
  text: string,
) {
  const composer = screen.getByRole("textbox", { name: "Message Athena" });
  await user.clear(composer);
  await user.type(composer, text);
  await user.click(screen.getByRole("button", { name: "Send" }));
}

describe("Athena product prototype", () => {
  it("uses one assistant entry and keeps knowledge management inside Athena", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const navigation = screen.getByRole("navigation", { name: "Primary" });
    expect(
      within(navigation)
        .getAllByRole("button")
        .map((item) => item.textContent?.trim()),
    ).toEqual(["Athena", "Test Management", "Low Code Automation"]);
    expect(
      screen.getByRole("heading", { name: "What can I do for you?" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Knowledge sources" }),
    ).toBeVisible();
    expect(await screen.findByText("life-underwriting-rules.md")).toBeVisible();
    expect(screen.getByText("health-disclosure-guide.pdf")).toBeVisible();
    expect(
      screen.getByRole("button", {
        name: "Create BDD test cases for life insurance underwriting",
      }),
    ).toBeVisible();
    expect(screen.queryByText("Intelligence Lab")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "问答" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Manage knowledge" }));
    expect(
      screen.getByRole("dialog", { name: "Knowledge Library" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "添加来源" })).toBeVisible();
  });

  it("creates BDD in chat and imports it as a Test Plan", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(user, "为寿险新单核保生成 BDD 测试用例");

    const artifact = screen.getByRole("article", {
      name: "Generated BDD test plan",
    });
    expect(
      within(artifact).getByText(
        "Feature: Life insurance application underwriting",
      ),
    ).toBeVisible();
    expect(
      within(artifact).getByText(/Given an adult applicant/),
    ).toBeVisible();
    await user.click(
      within(artifact).getByRole("button", { name: "Import to Test Plan" }),
    );

    expect(
      screen.getByRole("heading", { name: "Test Management" }),
    ).toBeVisible();
    const tabs = screen.getByRole("tablist", {
      name: "Test Management sections",
    });
    expect(
      within(tabs)
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["Test Plan", "Test Data"]);
    expect(
      screen.getByText("Life insurance application underwriting"),
    ).toBeVisible();
    expect(screen.getByText("Imported from Athena")).toBeVisible();
  });

  it("turns an automation request into BDD plus an editable Low Code Automation flow", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(user, "生成人寿保险投保的自动化脚本");

    const artifact = screen.getByRole("article", {
      name: "Generated automation",
    });
    expect(
      within(artifact).getByText("BDD scenario + 6 automation steps"),
    ).toBeVisible();
    await user.click(
      within(artifact).getByRole("button", {
        name: "Open in Low Code Automation",
      }),
    );

    expect(
      screen.getByRole("heading", {
        name: "Life insurance application automation",
      }),
    ).toBeVisible();
    expect(screen.getByText("life-policy-application.spec.ts")).toBeVisible();
    const target = screen.getByRole("textbox", {
      name: "Element for step 2",
    });
    fireEvent.change(target, {
      target: { value: "button[data-testid='start-application']" },
    });
    expect(target).toHaveValue("button[data-testid='start-application']");

    const stepCount = screen.getAllByRole("listitem", {
      name: /Automation step/,
    }).length;
    await user.click(screen.getByRole("button", { name: "Add step" }));
    expect(
      screen.getAllByRole("listitem", { name: /Automation step/ }),
    ).toHaveLength(stepCount + 1);
    await user.click(
      screen.getByRole("button", { name: `Delete step ${stepCount + 1}` }),
    );
    expect(
      screen.getAllByRole("listitem", { name: /Automation step/ }),
    ).toHaveLength(stepCount);
  });

  it("keeps the generated automation available for both destination handoffs", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(user, "生成人寿保险投保的自动化脚本");
    await user.click(
      screen.getByRole("button", { name: "Open in Low Code Automation" }),
    );
    await user.click(screen.getByRole("button", { name: "Athena" }));

    const artifact = screen.getByRole("article", {
      name: "Generated automation",
    });
    await user.click(
      within(artifact).getByRole("button", {
        name: "Import BDD as Test Plan",
      }),
    );

    expect(
      screen.getByText("Life insurance application underwriting"),
    ).toBeVisible();
    expect(screen.getByText("Imported from Athena")).toBeVisible();
  });

  it("never reuses an automation step id after delete and module navigation", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(
      screen.getByRole("button", { name: "Low Code Automation" }),
    );
    await user.click(screen.getByRole("button", { name: "Delete step 2" }));
    await user.click(screen.getByRole("button", { name: "Athena" }));
    await user.click(
      screen.getByRole("button", { name: "Low Code Automation" }),
    );
    await user.click(screen.getByRole("button", { name: "Add step" }));

    expect(
      screen.getAllByRole("listitem", { name: /Automation step/ }),
    ).toHaveLength(6);
    await user.click(screen.getByRole("button", { name: "Delete step 6" }));
    expect(
      screen.getAllByRole("listitem", { name: /Automation step/ }),
    ).toHaveLength(5);
  });

  it.each([
    [
      "Create a test plan for life insurance underwriting",
      "Generated BDD test plan",
    ],
    [
      "Automate life insurance applications with Playwright",
      "Generated automation",
    ],
  ])("routes common request wording: %s", async (prompt, artifactName) => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(user, prompt);

    expect(screen.getByRole("article", { name: artifactName })).toBeVisible();
  });

  it("does not treat a workflow question as an automation request", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(user, "What is the life underwriting workflow?");

    expect(
      screen.getByText("What is the life underwriting workflow?"),
    ).toBeVisible();
    expect(
      screen.queryByRole("article", { name: "Generated automation" }),
    ).not.toBeInTheDocument();
  });

  it("supports keyboard navigation between Test Management sections", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Test Management" }));
    expect(
      screen.getByText("Life policy application regression"),
    ).toBeVisible();
    expect(screen.getByText("Beneficiary maintenance")).toBeVisible();
    const testPlanTab = screen.getByRole("tab", { name: "Test Plan" });
    const testDataTab = screen.getByRole("tab", { name: "Test Data" });
    testPlanTab.focus();
    await user.keyboard("{ArrowRight}");

    expect(testDataTab).toHaveFocus();
    expect(testDataTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "Test Data" })).toBeVisible();
  });

  it("keeps ordinary questions in the chat conversation", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(user, "寿险投保需要什么资料？");

    expect(screen.getByText("寿险投保需要什么资料？")).toBeVisible();
    expect(
      screen.getByText(/根据当前选择的知识来源，寿险投保通常需要投保人/),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Import to Test Plan" }),
    ).not.toBeInTheDocument();
  });

  it("moves the composer from the centered start state to the conversation dock", async () => {
    const user = userEvent.setup();
    renderPrototype();

    const start = screen.getByRole("region", { name: "Start a conversation" });
    expect(
      within(start).getByRole("heading", { name: "What can I do for you?" }),
    ).toBeVisible();
    expect(
      within(start).getByRole("form", { name: "Message composer" }),
    ).toBeVisible();

    await sendMessage(user, "寿险投保需要什么资料？");

    expect(
      screen.queryByRole("region", { name: "Start a conversation" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("log", { name: "Conversation" })).toBeVisible();
    expect(
      screen.getByRole("form", { name: "Message composer" }),
    ).toBeVisible();
  });
});
