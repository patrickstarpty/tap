import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  answerResponse,
  citationPreview,
  fakeKnowledgeClient,
  retrievalCitation,
} from "../testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../testing/renderKnowledgeApp";
import { GroundedAnswer } from "./GroundedAnswer";

type RetrievalCitation = ReturnType<typeof retrievalCitation>;

function edgeCitation(
  citationId: string,
  overrides: Partial<{
    graphVersion: string;
    relationLabel: string;
    relationType: string;
  }> = {},
): RetrievalCitation {
  return {
    ...retrievalCitation(citationId),
    kind: "edge",
    edge: {
      edgeId: `edge-${citationId}`,
      graphVersion: overrides.graphVersion ?? "2",
      subject: { nodeId: "node-underwriting", label: "Underwriting review" },
      object: { nodeId: "node-disclosure", label: "Health disclosure" },
      relationType: overrides.relationType ?? "REQUIRES",
      relationLabel: overrides.relationLabel ?? "requires",
    },
  } as RetrievalCitation;
}

function mixedAnswer() {
  const answer = "第一段。\n\n第二段。\n\n第三段。";
  return answerResponse({
    answer,
    claims: [
      {
        claimId: "claim-1",
        text: "第一段。",
        answerStart: 0,
        answerEnd: 4,
        citationIds: ["citation-a"],
      },
      {
        claimId: "claim-2",
        text: "第二段。",
        answerStart: 6,
        answerEnd: 10,
        citationIds: ["edge-1"],
      },
      {
        claimId: "claim-3",
        text: "第三段。",
        answerStart: 12,
        answerEnd: 16,
        citationIds: ["citation-b"],
      },
    ],
    citations: [
      retrievalCitation("citation-a"),
      edgeCitation("edge-1"),
      retrievalCitation("citation-b"),
    ],
  });
}

describe("EdgeCitationChip (via GroundedAnswer)", () => {
  it("numbers chunk and edge citations independently", () => {
    renderKnowledgeApp(
      <GroundedAnswer
        locale="en"
        response={mixedAnswer()}
        onOpenCitation={() => undefined}
      />,
      { api: fakeKnowledgeClient() },
    );

    expect(
      screen.getByRole("button", { name: "Open source citation 1" }),
    ).toHaveTextContent("[1]");
    expect(
      screen.getByRole("button", { name: "Open relation citation R1" }),
    ).toHaveTextContent("[R1]");
    expect(
      screen.getByRole("button", { name: "Open source citation 2" }),
    ).toHaveTextContent("[2]");
  });

  it("shows the relation and snippet on hover", async () => {
    const api = fakeKnowledgeClient().withCitation(
      citationPreview({
        citationId: "edge-1",
        quote: "Health disclosure is required before underwriting review.",
      }),
    );
    renderKnowledgeApp(
      <GroundedAnswer
        locale="en"
        response={mixedAnswer()}
        onOpenCitation={() => undefined}
      />,
      { api },
    );

    const user = userEvent.setup();
    await user.hover(
      screen.getByRole("button", { name: "Open relation citation R1" }),
    );

    expect(
      await screen.findByText(
        "Underwriting review —requires→ Health disclosure",
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Health disclosure is required before underwriting review.",
      ),
    ).toBeInTheDocument();
  });

  it("still renders chips when the edge graph version is stale", async () => {
    const onOpenCitation = vi.fn();
    renderKnowledgeApp(
      <GroundedAnswer
        locale="en"
        response={answerResponse({
          answer: "第二段。",
          claims: [
            {
              claimId: "claim-2",
              text: "第二段。",
              answerStart: 0,
              answerEnd: 4,
              citationIds: ["edge-1"],
            },
          ],
          citations: [edgeCitation("edge-1", { graphVersion: "v1-stale" })],
        })}
        onOpenCitation={onOpenCitation}
      />,
      { api: fakeKnowledgeClient() },
    );

    const chip = screen.getByRole("button", {
      name: "Open relation citation R1",
    });
    expect(chip).toHaveTextContent("[R1]");
    const user = userEvent.setup();
    await user.click(chip);
    expect(onOpenCitation).toHaveBeenCalledWith("edge-1", chip);
  });
});
