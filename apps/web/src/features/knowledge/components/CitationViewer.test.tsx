import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  citationPreview,
  fakeKnowledgeClient,
  retrievalCitation,
} from "../testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../testing/renderKnowledgeApp";
import { CitationViewer } from "./CitationViewer";

describe("CitationViewer", () => {
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
