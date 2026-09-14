import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  document,
  fakeKnowledgeClient,
} from "../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../features/knowledge/testing/renderKnowledgeApp";
import { TapperPage } from "./TapperPage";
import { TapProductPrototype } from "../widgets/tap/TapProductPrototype";

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
  return renderKnowledgeApp(
    <TapProductPrototype conversationSource="fixture" />,
    { api },
  );
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

  afterEach(() => vi.unstubAllGlobals());

  it("restores the default Tapper page from durable Conversation APIs, not localStorage", async () => {
    window.localStorage.setItem(
      "tap.prototype.workspace.v2",
      JSON.stringify({
        version: 2,
        activeConversationId: "local-only",
        conversations: [
          {
            id: "local-only",
            title: "Local-only prompt",
            turns: [],
            modelId: "tapper-chat",
            selectedSourceIds: [],
            selectedAgentIds: [],
            selectedSkillIds: [],
          },
        ],
        artifacts: { automations: [], testPlans: [], runs: [] },
      }),
    );
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(input);
      if (/\/ai\/(agents|skills)$/u.test(request.url)) {
        return Response.json({ items: [] });
      }
      if (request.url.endsWith("/conversations?limit=20")) {
        return Response.json({
          items: [
            {
              conversationId: "conversation-1",
              title: "Durable prompt",
              createdAt: "2026-09-09T00:00:00Z",
              updatedAt: "2026-09-09T00:00:01Z",
            },
            {
              conversationId: "conversation-2",
              title: "Older durable prompt",
              createdAt: "2026-09-08T00:00:00Z",
              updatedAt: "2026-09-08T00:00:01Z",
            },
          ],
          nextCursor: null,
        });
      }
      if (request.url.endsWith("/conversation-1/events")) {
        return Response.json({
          items: [
            {
              eventId: "event-1",
              sequence: 1,
              turnId: "turn-1",
              eventType: "turn.abstained",
              payload: {
                answer: {
                  traceId: "trace-1",
                  queryPlanId: "plan-1",
                  contextSnapshotId: "context-1",
                  corpusVersion: "v1",
                  retrievalProfileId: "quick",
                  degradedMode: false,
                  answer: "",
                  abstained: true,
                  abstentionReason: "insufficient_evidence",
                  claims: [],
                  citations: [],
                },
              },
              occurredAt: "2026-09-09T00:00:01Z",
            },
          ],
        });
      }
      if (request.url.endsWith("/conversation-1/stream")) {
        return new Response("", {
          headers: { "content-type": "text/event-stream" },
        });
      }
      if (request.url.endsWith("/conversation-1")) {
        return Response.json({
          conversationId: "conversation-1",
          title: "Durable prompt",
          createdAt: "2026-09-09T00:00:00Z",
          updatedAt: "2026-09-09T00:00:01Z",
          turns: [
            {
              turnId: "turn-1",
              state: "abstained",
              attempt: 1,
              inputSnapshotDigest: `sha256:${"1".repeat(64)}`,
              answerEvidenceSnapshotId: "answer-1",
              answerEvidenceSnapshotDigest: `sha256:${"2".repeat(64)}`,
              input: {
                message: "Durable prompt",
                modelAlias: "tapper-chat",
                sourceRevisionIds: [],
                documentRevisionIds: [],
                agentRevisionId: null,
                skillRevisionIds: [],
              },
            },
          ],
        });
      }
      return Response.json({ items: [] });
    });

    const first = renderKnowledgeApp(<TapperPage />, {
      api: fakeKnowledgeClient(),
    });
    expect(
      within(
        await screen.findByRole("log", { name: "Conversation" }),
      ).getByText("Durable prompt"),
    ).toBeVisible();
    expect(screen.queryByText("Local-only prompt")).not.toBeInTheDocument();
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: /Older durable prompt/u }),
    ).toBeVisible();
    first.unmount();
    renderKnowledgeApp(<TapperPage />, { api: fakeKnowledgeClient() });
    await waitFor(() =>
      expect(
        within(screen.getByRole("log", { name: "Conversation" })).getByText(
          "Durable prompt",
        ),
      ).toBeVisible(),
    );
  });

  it("never substitutes prototype copy when a completed API Turn has no answer evidence event", async () => {
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(input);
      if (/\/ai\/(agents|skills)$/u.test(request.url))
        return Response.json({ items: [] });
      if (request.url.endsWith("/conversations?limit=20")) {
        return Response.json({
          items: [
            {
              conversationId: "conversation-1",
              title: "No evidence",
              createdAt: "2026-09-09T00:00:00Z",
              updatedAt: "2026-09-09T00:00:01Z",
            },
          ],
          nextCursor: null,
        });
      }
      if (request.url.endsWith("/conversation-1/events")) {
        return Response.json({ items: [] });
      }
      if (request.url.endsWith("/conversation-1/stream")) {
        return new Response("", {
          headers: { "content-type": "text/event-stream" },
        });
      }
      if (request.url.endsWith("/conversation-1")) {
        return Response.json({
          conversationId: "conversation-1",
          title: "No evidence",
          createdAt: "2026-09-09T00:00:00Z",
          updatedAt: "2026-09-09T00:00:01Z",
          turns: [
            {
              turnId: "turn-1",
              state: "completed",
              attempt: 1,
              inputSnapshotDigest: `sha256:${"1".repeat(64)}`,
              answerEvidenceSnapshotId: "answer-1",
              answerEvidenceSnapshotDigest: `sha256:${"2".repeat(64)}`,
              input: {
                message: "No evidence",
                modelAlias: "tapper-chat",
                sourceRevisionIds: [],
                documentRevisionIds: [],
                resolvedResources: [],
                agentRevisionId: null,
                agentLabel: null,
                skillRevisionIds: [],
                skillLabels: [],
              },
            },
          ],
        });
      }
      return Response.json({ items: [] });
    });

    renderKnowledgeApp(<TapperPage />, { api: fakeKnowledgeClient() });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Answer evidence is unavailable",
    );
    expect(
      screen.queryByText(/This prototype response says/u),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  it("renders a canceled durable Turn as stopped even when partial evidence arrived", async () => {
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(input);
      if (/\/ai\/(agents|skills)$/u.test(request.url))
        return Response.json({ items: [] });
      if (request.url.endsWith("/conversations?limit=20")) {
        return Response.json({
          items: [
            {
              conversationId: "conversation-1",
              title: "Canceled prompt",
              createdAt: "2026-09-09T00:00:00Z",
              updatedAt: "2026-09-09T00:00:01Z",
            },
          ],
          nextCursor: null,
        });
      }
      if (request.url.endsWith("/conversation-1/events")) {
        return Response.json({
          items: [
            {
              eventId: "event-1",
              sequence: 1,
              turnId: "turn-1",
              eventType: "citation.resolved",
              payload: {
                citation: {
                  citationId: "citation-1",
                  sourceId: "source-1",
                  documentId: "document-1",
                  revisionId: "revision-1",
                },
              },
              occurredAt: "2026-09-09T00:00:01Z",
            },
          ],
        });
      }
      if (request.url.endsWith("/conversation-1")) {
        return Response.json({
          conversationId: "conversation-1",
          title: "Canceled prompt",
          createdAt: "2026-09-09T00:00:00Z",
          updatedAt: "2026-09-09T00:00:01Z",
          turns: [
            {
              turnId: "turn-1",
              state: "canceled",
              attempt: 1,
              inputSnapshotDigest: `sha256:${"1".repeat(64)}`,
              answerEvidenceSnapshotId: null,
              answerEvidenceSnapshotDigest: null,
              input: {
                message: "Canceled prompt",
                modelAlias: "tapper-chat",
                sourceRevisionIds: [],
                documentRevisionIds: [],
                resolvedResources: [],
                agentRevisionId: null,
                agentLabel: null,
                skillRevisionIds: [],
                skillLabels: [],
              },
            },
          ],
        });
      }
      return new Response("", {
        headers: { "content-type": "text/event-stream" },
      });
    });

    renderKnowledgeApp(<TapperPage />, { api: fakeKnowledgeClient() });

    expect(await screen.findByText("Generation stopped.")).toBeVisible();
    expect(
      screen.queryByText("回答格式无法核验，请重新提问。"),
    ).not.toBeInTheDocument();
  });

  it("keeps ready validation details out of the primary workspace", async () => {
    const user = userEvent.setup();
    renderPrototype();
    expect(
      screen.queryByRole("status", { name: "Validation Mode" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(
      screen.queryByRole("status", { name: "Validation Mode" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Test Management" }));
    expect(
      screen.queryByRole("status", { name: "Validation Mode" }),
    ).not.toBeInTheDocument();
  });

  it("shows TAP platform and Tapper workspace identities", () => {
    renderKnowledgeApp(<TapperPage />, { api: fakeKnowledgeClient() });

    expect(screen.getByLabelText("TAP platform")).toHaveTextContent(/^TAP$/);
    const entry = screen.getByRole("button", { name: "Tapper" });
    expect(
      entry.querySelector('img[src*="tapper-listening-avatar-color.svg"]'),
    ).not.toBeNull();
    const heading = screen.getByRole("heading", { name: "Tapper" });
    expect(
      heading.querySelector('img[src*="tapper-listening-avatar-color.svg"]'),
    ).toBeNull();
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
    ).toEqual([
      "Tapper",
      "Test Management",
      "Low Code Automation",
      "Test Observability",
    ]);
    expect(
      within(screen.getByRole("navigation", { name: "Tapper tools" }))
        .getAllByRole("button")
        .map((item) => item.textContent?.trim()),
    ).toEqual(["New chat", "Agents", "Skills", "Library"]);
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

  it("keeps the explicit demo fixture Conversation across modules and remounts", async () => {
    const user = userEvent.setup();
    const firstRender = renderPrototype();
    const message = "Create a browser automation for policy submission";

    await sendMessage(user, message);
    const history = screen.getByRole("navigation", { name: "Chat history" });
    expect(
      within(history).getByRole("button", {
        name: `${message}`,
      }),
    ).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Agents" }));
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${message}` }),
    ).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(screen.getByRole("button", { name: "Tapper" }));
    await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${message}` }),
    ).toHaveAttribute("aria-current", "page");

    firstRender.unmount();
    renderPrototype();
    expect(
      screen.getByText(message, { selector: ".tap-user-message" }),
    ).toBeVisible();
    expect(
      within(
        screen.getByRole("navigation", { name: "Chat history" }),
      ).getByRole("button", { name: `${message}` }),
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

    await user.click(screen.getByRole("row", { name: /AUTO-101/ }));
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
    await user.click(screen.getByRole("row", { name: /AUTO-102/ }));
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
      screen.getByText("What is the life underwriting workflow?", {
        selector: ".tap-user-message",
      }),
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

  it("opens plan-scoped Test Observability from a Test Plan quality summary", async () => {
    const user = userEvent.setup();
    renderPrototype();

    await user.click(screen.getByRole("button", { name: "Test Management" }));
    await user.click(
      screen.getByRole("row", {
        name: /Life insurance application underwriting/,
      }),
    );

    const summary = screen.getByRole("region", { name: "Quality summary" });
    expect(within(summary).getByText("Pass rate")).toBeVisible();
    expect(within(summary).getByText("Failed tests")).toBeVisible();
    expect(within(summary).getByText("Flaky tests")).toBeVisible();
    expect(within(summary).getByText("Latest build")).toBeVisible();

    await user.click(
      within(summary).getByRole("button", {
        name: "Open Test Observability",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "Demo Dashboard" }),
    ).toBeVisible();
    expect(screen.getByText("Test Plan: TP-101")).toBeVisible();
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

    expect(
      screen.getByText("寿险投保需要什么资料？", {
        selector: ".tap-user-message",
      }),
    ).toBeVisible();
    expect(
      screen.getByText(/此轮对话未选择知识上下文。回答仅基于当前可用信息/),
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
      screen.getByText(/No knowledge context was selected for this turn/),
    ).toBeVisible();
    expect(screen.queryByText(/此轮对话未选择知识上下文/)).toBeNull();
  });

  it("localizes product workspaces without losing saved conversation data", async () => {
    const user = userEvent.setup();
    renderPrototype();
    const prompt = "What evidence is needed for life underwriting?";

    await sendMessage(user, prompt);
    await user.click(screen.getByRole("button", { name: "中文" }));

    expect(
      screen.getByText(prompt, { selector: ".tap-user-message" }),
    ).toBeVisible();
    await user.click(screen.getByRole("button", { name: "知识库" }));
    await user.click(screen.getByRole("tab", { name: "文档列表" }));
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
    await user.click(screen.getByRole("button", { name: "展开侧边栏" }));
    await user.click(
      within(screen.getByRole("navigation", { name: "对话历史" })).getByRole(
        "button",
        {
          name: /What evidence is needed for life underwriting\?/,
        },
      ),
    );
    expect(
      screen.getByText(prompt, { selector: ".tap-user-message" }),
    ).toBeVisible();
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
