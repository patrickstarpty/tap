import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  citationPreview,
  fakeKnowledgeClient,
  retrievalCitation,
} from "../testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../testing/renderKnowledgeApp";
import { CitationViewer } from "./CitationViewer";

describe("CitationViewer", () => {
  it("shows the reviewed flowchart node's image region", async () => {
    const anchor = {
      type: "document" as const,
      headingPath: ["approval.png"],
      page: null,
      bbox: [10, 20, 50, 60],
      startOffset: 0,
      endOffset: 8,
    };
    const citation = retrievalCitation("citation-a", {
      source: { ...retrievalCitation().source, anchor },
    });
    const api = fakeKnowledgeClient().withCitation(
      citationPreview({ filename: "approval.png", anchor }),
    );
    renderKnowledgeApp(
      <CitationViewer
        active={{ citation, generation: 1, id: "citation-a" }}
        onClose={() => undefined}
      />,
      { api },
    );

    expect(await screen.findByText("10, 20, 50, 60")).toBeVisible();
    expect(screen.getByText("图中区域（像素）")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "经核对的流程图解析" }),
    ).toBeVisible();
  });

  it("returns focus to the citation trigger when closed", async () => {
    const trigger = document.createElement("button");
    trigger.textContent = "Open citation";
    document.body.append(trigger);
    trigger.focus();
    renderKnowledgeApp(
      <CitationViewer
        active={{
          citation: retrievalCitation("citation-a"),
          generation: 1,
          id: "citation-a",
        }}
        returnFocusTo={trigger}
        onClose={() => undefined}
      />,
      { api: fakeKnowledgeClient() },
    );
    await userEvent.click(screen.getByRole("button", { name: "关闭原文" }));
    expect(trigger).toHaveFocus();
    trigger.remove();
  });
  it("uses English UI copy and identifies a stale historical source", () => {
    renderKnowledgeApp(
      <CitationViewer
        locale="en"
        active={{
          citation: retrievalCitation("citation-a"),
          generation: 1,
          id: "citation-a",
        }}
        historicalQuery={{
          isError: true,
          isFetching: false,
          data: undefined,
          error: Object.assign(new Error("Citation request failed"), {
            name: "ConversationClientError",
            status: 404,
            code: "citation-stale",
          }),
          refetch: async () => undefined,
        }}
        onClose={() => undefined}
      />,
      { api: fakeKnowledgeClient() },
    );
    expect(screen.getByRole("heading", { name: "Source text" })).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("no longer resolves");
    expect(screen.getByRole("alert")).toHaveTextContent("withdrawn");
    expect(screen.queryByText("原文核验未完成")).not.toBeInTheDocument();
  });
  it("accepts a governed preview when Source and Document identities differ", async () => {
    const sourceId = `src_${"1".repeat(32)}`;
    const api = fakeKnowledgeClient().withCitation(
      citationPreview({ documentId: "document-1" }),
    );
    renderKnowledgeApp(
      <CitationViewer
        active={{
          citation: retrievalCitation("citation-a", {
            source: {
              ...retrievalCitation().source,
              sourceId,
            },
          }),
          generation: 1,
          id: "citation-a",
        }}
        onClose={() => undefined}
      />,
      { api },
    );

    expect(await screen.findByText("policy.md")).toBeVisible();
    expect(screen.getByRole("link", { name: "打开来源" })).toHaveAttribute(
      "href",
      `#source-${sourceId}`,
    );
  });
});
