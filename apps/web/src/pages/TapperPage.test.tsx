import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import {
  document,
  fakeKnowledgeClient,
} from "../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../features/knowledge/testing/renderKnowledgeApp";
import { TapperPage } from "./TapperPage";

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
  return renderKnowledgeApp(<TapperPage />, { api });
}

async function sendMessage(
  user: ReturnType<typeof userEvent.setup>,
  text: string,
) {
  const composer = screen.getByRole("textbox", { name: "Message Tapper" });
  await user.clear(composer);
  await user.type(composer, text);
  await user.click(screen.getByRole("button", { name: "Send" }));
}

describe("Tapper product prototype", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("shows TAP platform and Tapper workspace identities", () => {
    renderKnowledgeApp(<TapperPage />, { api: fakeKnowledgeClient() });

    expect(screen.getByLabelText("TAP platform")).toHaveTextContent(/^TAP$/);
    const entry = screen.getByRole("button", { name: "Tapper" });
    expect(
      entry.querySelector('img[src*="tapper-mark-ink.svg"]'),
    ).not.toBeNull();
    const heading = screen.getByRole("heading", { name: "Tapper" });
    expect(
      heading.querySelector('img[src*="tapper-wordmark-ink.svg"]'),
    ).not.toBeNull();
  });

  it("uses the integrated Tapper navigation and keeps sources inside Tapper", async () => {
    renderPrototype();

    const navigation = screen.getByRole("navigation", { name: "Product" });
    expect(
      within(navigation)
        .getAllByRole("button")
        .map((item) => item.getAttribute("aria-label")),
    ).toEqual(["Tapper", "Test Management", "Low Code Automation"]);
    expect(
      within(screen.getByRole("navigation", { name: "Tapper tools" }))
        .getAllByRole("button")
        .map((item) => item.textContent?.trim()),
    ).toEqual(["New chat", "Agent", "Skills", "Library"]);
    expect(
      screen.getByRole("heading", { name: "What can I do for you?" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Knowledge sources" }),
    ).toBeVisible();
    expect(await screen.findByText("life-underwriting-rules.md")).toBeVisible();
    expect(screen.getByText("health-disclosure-guide.pdf")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Manage knowledge" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Create BDD test cases for life insurance underwriting",
      }),
    ).toBeVisible();
    expect(screen.queryByText("Intelligence Lab")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "问答" }),
    ).not.toBeInTheDocument();
  });

  it("keeps the active Conversation in history across modules and page remounts", async () => {
    const user = userEvent.setup();
    const firstRender = renderPrototype();
    const message = "Create a browser automation for policy submission";

    await sendMessage(user, message);
    const history = screen.getByRole("navigation", { name: "Chat history" });
    expect(
      within(history).getByRole("button", {
        name: `${message} · Conversation 1`,
      }),
    ).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Agent" }));
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${message} · Conversation 1` }),
    ).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Tapper" }));
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${message} · Conversation 1` }),
    ).toHaveAttribute("aria-current", "page");

    firstRender.unmount();
    renderPrototype();
    expect(screen.getByText(message)).toBeVisible();
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${message} · Conversation 1` }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("shows BDD-to-action mapping and projects a linked Run into Test Plan history", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(
      user,
      "Generate a browser automation for policy submission",
    );
    const request = screen.getByRole("article", {
      name: "Generated automation",
    });
    await user.click(
      within(request).getByRole("button", { name: "Create Test Plan first" }),
    );
    await user.click(
      within(request).getByRole("button", {
        name: "Generate linked automation",
      }),
    );
    await user.click(
      within(request).getByRole("button", {
        name: "Open in Low Code Automation",
      }),
    );

    const mappedStep = screen.getByRole("article", { name: "BDD step 2" });
    expect(within(mappedStep).getByText("Automation actions")).toBeVisible();
    expect(within(mappedStep).getByText("Click")).toBeVisible();
    expect(within(mappedStep).getAllByText("Send keys")).not.toHaveLength(0);

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Execution Agent" }),
      "ado-web-agent-03",
    );
    await user.click(screen.getByRole("button", { name: "Run automation" }));
    const automationHistory = screen.getByRole("region", {
      name: "Automation run history",
    });
    expect(
      within(automationHistory).getByText("Completed · Simulated"),
    ).toBeVisible();
    const runId = within(automationHistory).getByText(/^RUN-/).textContent;

    await user.click(screen.getByRole("button", { name: /Open Test Plan/ }));
    const planHistory = screen.getByRole("region", {
      name: "Test Plan execution history",
    });
    expect(within(planHistory).getByText(runId!)).toBeVisible();
    expect(
      within(planHistory).getByText("Completed · Simulated"),
    ).toBeVisible();
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
    const importedCell = within(
      screen.getByRole("table", { name: "Test plan list" }),
    ).getByText("Imported from Tapper");
    const importedRow = importedCell.closest<HTMLElement>('[role="row"]');
    expect(importedRow).not.toBeNull();
    expect(
      within(importedRow!).getByText("Life insurance application underwriting"),
    ).toBeVisible();
  });

  it("turns an automation request into BDD plus an editable Low Code Automation flow", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(user, "生成人寿保险投保的自动化脚本");

    const artifact = screen.getByRole("article", {
      name: "Generated automation",
    });
    expect(
      within(artifact).getByText("Create a Test Plan first?"),
    ).toBeVisible();
    await user.click(
      within(artifact).getByRole("button", { name: "Skip Test Plan" }),
    );
    await user.click(
      within(artifact).getByRole("button", { name: "Create Web automation" }),
    );
    expect(within(artifact).getByText("Send keys")).toBeVisible();
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
    const mappedStep = screen.getByRole("article", { name: "BDD step 2" });
    expect(within(mappedStep).getByText("Click")).toBeVisible();
    expect(within(mappedStep).getAllByText("Send keys")).not.toHaveLength(0);
    await user.click(
      within(mappedStep).getByRole("button", {
        name: "Edit automation actions 2",
      }),
    );
    const target = screen.getByRole("textbox", {
      name: "Locator or target 1 for BDD step 2",
    });
    await user.clear(target);
    await user.type(target, "coverage-amount-field");
    expect(target).toHaveValue("coverage-amount-field");

    const stepCount = screen.getAllByRole("article", {
      name: /BDD step/,
    }).length;
    await user.click(screen.getByRole("button", { name: "Add BDD step" }));
    expect(screen.getAllByRole("article", { name: /BDD step/ })).toHaveLength(
      stepCount + 1,
    );
  });

  it("opens Low Code Automation as an asset library", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(
      screen.getByRole("button", { name: "Low Code Automation" }),
    );

    const table = screen.getByRole("table", { name: "Low Code Automation" });
    expect(within(table).getByRole("row", { name: /AUTO-101/ })).toBeVisible();
    expect(within(table).getByRole("row", { name: /AUTO-102/ })).toBeVisible();
    expect(
      screen.getByRole("button", { name: /New automation/ }),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Open AUTO-101" }));
    expect(
      screen.getByRole("heading", {
        name: "Life insurance application automation",
      }),
    ).toBeVisible();
  });

  it("infers Mobile automation in Tapper and uses device execution", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(
      user,
      "Create a mobile automation for a life insurance application",
    );
    const artifact = screen.getByRole("article", {
      name: "Generated automation",
    });
    await user.click(
      within(artifact).getByRole("button", { name: "Skip Test Plan" }),
    );
    expect(within(artifact).getByText(/Mobile · Not linked/)).toBeVisible();
    expect(within(artifact).getByText("Wait")).toBeVisible();
    expect(within(artifact).queryByText("Navigate")).not.toBeInTheDocument();
    await user.click(
      within(artifact).getByRole("button", {
        name: "Open in Low Code Automation",
      }),
    );

    expect(screen.getByText(/Mobile · Ready/)).toBeVisible();
    expect(
      screen.getByRole("combobox", { name: "Run platform" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("combobox", { name: "Execution Agent" }),
    ).not.toBeInTheDocument();
  });

  it("asks for Web or Mobile when Tapper cannot infer the channel", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(
      user,
      "Create an automation script for policy submission",
    );
    const artifact = screen.getByRole("article", {
      name: "Generated automation",
    });
    await user.click(
      within(artifact).getByRole("button", { name: "Skip Test Plan" }),
    );

    expect(within(artifact).getByText("Choose Web or Mobile")).toBeVisible();
    expect(
      within(artifact).queryByRole("button", {
        name: "Open in Low Code Automation",
      }),
    ).not.toBeInTheDocument();
    await user.click(
      within(artifact).getByRole("button", { name: "Create Web automation" }),
    );
    expect(
      within(artifact).getByRole("button", {
        name: "Open in Low Code Automation",
      }),
    ).toBeVisible();
  });

  it("asks for an explicit channel when generation intent is ambiguous", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(
      screen.getByRole("button", { name: "Low Code Automation" }),
    );
    await user.click(screen.getByRole("button", { name: "New automation" }));
    await user.type(
      screen.getByRole("textbox", { name: "Automation title" }),
      "Cross-channel onboarding",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Describe what to automate" }),
      "Run onboarding in a browser and Android app",
    );
    await user.click(screen.getByRole("button", { name: "Generate BDD" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "This could be Web and Mobile. Choose a type to continue.",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Automation type" }),
      "web",
    );
    await user.click(screen.getByRole("button", { name: "Generate BDD" }));
    expect(
      screen.getByRole("heading", { name: "Cross-channel onboarding" }),
    ).toBeVisible();
  });

  it("creates a manual Automation with an editable starter BDD step", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(
      screen.getByRole("button", { name: "Low Code Automation" }),
    );
    await user.click(screen.getByRole("button", { name: "New automation" }));
    await user.type(
      screen.getByRole("textbox", { name: "Automation title" }),
      "Manual quote review",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Automation type" }),
      "web",
    );
    await user.click(
      screen.getByRole("button", { name: "Create blank automation" }),
    );

    expect(
      screen.getByRole("heading", { name: "Manual quote review" }),
    ).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "BDD step text 1" }),
    ).toHaveValue("Describe the starting context");
  });

  it("keeps generated Automation assets isolated across chat turns", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(
      user,
      "Generate an automation script for policy submission",
    );
    let artifact = screen.getByRole("article", {
      name: "Generated automation",
    });
    await user.click(
      within(artifact).getByRole("button", { name: "Skip Test Plan" }),
    );
    await user.click(
      within(artifact).getByRole("button", { name: "Create Web automation" }),
    );
    await user.click(
      within(artifact).getByRole("button", {
        name: "Open in Low Code Automation",
      }),
    );
    const firstStep = screen.getByRole("textbox", { name: "BDD step text 1" });
    await user.clear(firstStep);
    await user.type(firstStep, "an edited first conversation step");

    await user.click(screen.getByRole("button", { name: "Tapper" }));
    await sendMessage(
      user,
      "Create another automation script for a life policy",
    );
    const artifacts = screen.getAllByRole("article", {
      name: "Generated automation",
    });
    artifact = artifacts.at(-1)!;
    await user.click(
      within(artifact).getByRole("button", { name: "Skip Test Plan" }),
    );
    await user.click(
      within(artifact).getByRole("button", { name: "Create Web automation" }),
    );
    await user.click(
      within(artifact).getByRole("button", {
        name: "Open in Low Code Automation",
      }),
    );

    expect(
      screen.getByRole("textbox", { name: "BDD step text 1" }),
    ).toHaveValue("an adult applicant starts a term life application");
  });

  it("keeps both linked artifact handoffs in the Tapper response", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(user, "生成人寿保险投保的自动化脚本");
    const artifact = screen.getByRole("article", {
      name: "Generated automation",
    });
    await user.click(
      within(artifact).getByRole("button", {
        name: "Create Test Plan first",
      }),
    );
    await user.click(
      within(artifact).getByRole("button", {
        name: "Generate linked automation",
      }),
    );
    await user.click(
      within(artifact).getByRole("button", { name: "Create Web automation" }),
    );
    expect(
      within(artifact).getByRole("button", { name: "Open Test Plan" }),
    ).toBeVisible();
    expect(
      within(artifact).getByRole("button", {
        name: "Open in Low Code Automation",
      }),
    ).toBeVisible();
    await user.click(
      within(artifact).getByRole("button", { name: "Open Test Plan" }),
    );
    expect(
      screen.getByRole("heading", {
        name: "Life insurance application underwriting",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /Open Automation AUTO-/ }),
    ).toBeVisible();
  });

  it("gates Mobile execution on a supported platform and available device", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(
      screen.getByRole("button", { name: "Low Code Automation" }),
    );
    await user.click(screen.getByRole("button", { name: "Open AUTO-102" }));
    const run = screen.getByRole("button", { name: "Run automation" });
    expect(run).toBeDisabled();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Run platform" }),
      "ios",
    );
    expect(run).toBeDisabled();
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Device" }),
      "iphone-15",
    );
    expect(run).toBeEnabled();
    await user.click(run);
    expect(screen.getByText("Completed · Simulated")).toBeVisible();
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

  it.each([
    ["BDD test plan for life underwriting", "Generated BDD test plan"],
    ["I need BDD test cases for life underwriting", "Generated BDD test plan"],
    ["Automation script for a life application", "Generated automation"],
    ["寿险核保 BDD 测试计划", "Generated BDD test plan"],
    ["寿险投保自动化脚本", "Generated automation"],
  ])(
    "routes noun-form requests without a creation verb: %s",
    async (prompt, artifactName) => {
      const user = userEvent.setup();
      renderPrototype();

      await sendMessage(user, prompt);

      expect(screen.getByRole("article", { name: artifactName })).toBeVisible();
    },
  );

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
      screen.getByText("Life insurance application underwriting"),
    ).toBeVisible();
    expect(
      screen.getByText("Beneficiary designation validation"),
    ).toBeVisible();
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

    await user.click(screen.getByRole("button", { name: "中文" }));
    await user.type(
      screen.getByRole("textbox", { name: "向 Tapper 发送消息" }),
      "寿险投保需要什么资料？",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    expect(screen.getByText("寿险投保需要什么资料？")).toBeVisible();
    expect(
      screen.getByText(/此轮对话未选择知识上下文。此原型输出使用内置演示内容/),
    ).toBeVisible();
    expect(screen.getByRole("region", { name: "Tapper 助手" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Import to Test Plan" }),
    ).not.toBeInTheDocument();
  });

  it("localizes a Chinese automation response and its action summary", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "中文" }));
    await user.type(
      screen.getByRole("textbox", { name: "向 Tapper 发送消息" }),
      "为寿险投保申请生成自动化脚本",
    );
    await user.click(screen.getByRole("button", { name: "发送" }));

    const artifact = screen.getByRole("article", {
      name: "生成的自动化流程",
    });
    expect(within(artifact).getByText("先创建测试计划吗？")).toBeVisible();
    await user.click(
      within(artifact).getByRole("button", { name: "暂不创建测试计划" }),
    );
    await user.click(
      within(artifact).getByRole("button", { name: "创建 Web 自动化" }),
    );
    expect(within(artifact).getByText("Navigate")).toBeVisible();
    expect(within(artifact).getByText("Click")).toBeVisible();
    expect(within(artifact).getByText("Send keys")).toBeVisible();
    expect(within(artifact).getByText("Assert")).toBeVisible();
    expect(within(artifact).getByText(/场景：完整申请进入核保/)).toBeVisible();
  });

  it("answers an English question in English by default", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await sendMessage(
      user,
      "What information is needed for a life insurance application?",
    );

    expect(
      screen.getByText(
        /No knowledge context was selected for this turn. This prototype output uses built-in demo content/,
      ),
    ).toBeVisible();
    expect(screen.queryByText(/此轮对话未选择知识上下文/)).toBeNull();
  });

  it("localizes product workspaces without losing saved conversation data", async () => {
    const user = userEvent.setup();
    renderPrototype();
    const prompt = "What evidence is needed for life underwriting?";

    await sendMessage(user, prompt);
    await user.click(screen.getByRole("button", { name: "中文" }));

    expect(screen.getByText(prompt)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "知识库" }));
    expect(screen.getAllByText("知识来源 · 已就绪")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "测试管理" }));
    expect(screen.getByRole("heading", { name: "测试管理" })).toBeVisible();
    expect(screen.getByText("2 个测试计划")).toBeVisible();
    expect(screen.getByRole("table", { name: "测试计划列表" })).toBeVisible();
    expect(
      within(screen.getByRole("tablist", { name: "测试管理分区" }))
        .getAllByRole("tab")
        .map((tab) => tab.textContent),
    ).toEqual(["测试计划", "测试数据"]);

    await user.click(screen.getByRole("button", { name: "低代码自动化" }));
    expect(screen.getByRole("heading", { name: "低代码自动化" })).toBeVisible();
    expect(screen.getByRole("table", { name: "低代码自动化" })).toBeVisible();
    expect(screen.getByRole("button", { name: /新建自动化/ })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Tapper" }));
    await user.click(
      screen.getByRole("button", {
        name: /What evidence is needed for life underwriting\?/,
      }),
    );
    expect(screen.getByText(prompt)).toBeVisible();
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

    const composer = within(start).getByRole("textbox", {
      name: "Message Tapper",
    });
    await user.type(composer, "寿险投保需要什么资料？");
    await user.keyboard("{Enter}");

    expect(
      screen.queryByRole("region", { name: "Start a conversation" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("log", { name: "Conversation" })).toBeVisible();
    expect(
      screen.getByRole("form", { name: "Message composer" }),
    ).toBeVisible();
    expect(
      screen.getByRole("textbox", { name: "Message Tapper" }),
    ).toHaveFocus();
  });
});
