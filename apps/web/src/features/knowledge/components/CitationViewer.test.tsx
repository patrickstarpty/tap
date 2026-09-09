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
