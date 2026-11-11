import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { WORKSPACE_COPY } from "./copy";
import { TapperChat } from "./TapperChat";

vi.mock("../../../features/knowledge/api/modelCatalog", () => ({
  useModelCatalog: () => ({
    data: {
      defaultAlias: "qwen-plus",
      items: [{ alias: "qwen-plus", capabilities: ["chat", "structured"] }],
    },
    isError: false,
  }),
}));
vi.mock("../../../features/knowledge/api/queries", () => ({
  usePromptSuggestionsQuery: () => ({
    data: { items: [] },
    isError: false,
    isLoading: false,
  }),
}));

it("keeps the natural-language draft when a browser run needs a start URL", async () => {
  const send = vi.fn().mockResolvedValue(true);
  const change = vi.fn();
  render(
    <TapperChat
      projectId="project-1"
      agents={[]}
      conversation={{
        id: "draft",
        title: "New chat",
        turns: [],
        modelId: "qwen-plus",
        selectedSourceIds: [],
        selectedAgentIds: [],
        selectedSkillIds: [],
      }}
      copy={WORKSPACE_COPY.en}
      locale="en"
      message="Find Ada's quote"
      onMessageChange={change}
      onModelChange={vi.fn()}
      onPickSuggestion={vi.fn()}
      onSend={send}
      onToggleAgent={vi.fn()}
      onToggleSkill={vi.fn()}
      onToggleSource={vi.fn()}
      renderAssistantTurn={vi.fn()}
      skills={[]}
      sources={[]}
      browserMode
      browserAvailable
      browserStartUrl=""
      onBrowserStartUrlChange={vi.fn()}
      onBrowserModeChange={vi.fn()}
      browserNotes=""
      onBrowserNotesChange={vi.fn()}
    />,
  );
  await userEvent.click(
    screen.getByRole("button", { name: WORKSPACE_COPY.en.chat.send }),
  );
  expect(screen.getByRole("alert")).toHaveTextContent("Start URL");
  expect(
    screen.getByRole("textbox", { name: WORKSPACE_COPY.en.chat.messageTapper }),
  ).toHaveValue("Find Ada's quote");
  expect(send).not.toHaveBeenCalled();
});

it("sends a browser goal without a checkbox, selected agent, or manual configuration", async () => {
  const send = vi.fn().mockResolvedValue(true);
  render(
    <TapperChat
      projectId="project-1"
      agents={[]}
      conversation={{
        id: "draft",
        title: "New chat",
        turns: [],
        modelId: "qwen-plus",
        selectedSourceIds: [],
        selectedAgentIds: [],
        selectedSkillIds: [],
      }}
      copy={WORKSPACE_COPY.en}
      locale="en"
      message="Get a quote at https://example.test/"
      onMessageChange={vi.fn()}
      onModelChange={vi.fn()}
      onPickSuggestion={vi.fn()}
      onSend={send}
      onToggleAgent={vi.fn()}
      onToggleSkill={vi.fn()}
      onToggleSource={vi.fn()}
      renderAssistantTurn={vi.fn()}
      skills={[]}
      sources={[]}
      browserAvailable
    />,
  );
  expect(screen.queryByRole("checkbox", { name: "Browser run" })).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: WORKSPACE_COPY.en.chat.send }),
  );
  expect(send).toHaveBeenCalledOnce();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("blocks unreviewed required fields even when submitting with Enter", async () => {
  const send = vi.fn().mockResolvedValue(true);
  const change = vi.fn();
  render(
    <TapperChat
      projectId="project-1"
      agents={[]}
      conversation={{
        id: "draft",
        title: "New chat",
        turns: [],
        modelId: "qwen-plus",
        selectedSourceIds: [],
        selectedAgentIds: [],
        selectedSkillIds: [],
      }}
      copy={WORKSPACE_COPY.en}
      locale="en"
      message="Find Ada's quote"
      onMessageChange={change}
      onModelChange={vi.fn()}
      onPickSuggestion={vi.fn()}
      onSend={send}
      onToggleAgent={vi.fn()}
      onToggleSkill={vi.fn()}
      onToggleSource={vi.fn()}
      renderAssistantTurn={vi.fn()}
      skills={[]}
      sources={[]}
      browserAcceptanceDraft={{
        reviewed: false,
        fields: [
          {
            id: "name",
            value: "Ada",
            readbackValue: "Ada",
            attribute: "value",
            targetTexts: ["Customer"],
          },
        ],
      }}
      browserMode
      browserAvailable
      browserStartUrl="https://example.test"
      onBrowserStartUrlChange={vi.fn()}
      onBrowserModeChange={vi.fn()}
      browserNotes=""
      onBrowserNotesChange={vi.fn()}
    />,
  );
  expect(
    screen.getByRole("button", { name: WORKSPACE_COPY.en.chat.send }),
  ).toBeDisabled();
  fireEvent.submit(
    screen.getByRole("form", { name: WORKSPACE_COPY.en.chat.messageComposer }),
  );
  expect(send).not.toHaveBeenCalled();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "Review every required input",
  );
});
