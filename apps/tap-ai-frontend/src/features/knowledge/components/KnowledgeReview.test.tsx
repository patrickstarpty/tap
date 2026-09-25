import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { KnowledgeReviewDetail } from "../api/types";
import { KnowledgeClientError } from "../api/client";
import { fakeKnowledgeClient } from "../testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../testing/renderKnowledgeApp";
import { KnowledgeReview } from "./KnowledgeReview";

const REVISION = "rev_01JABCDEF";
const ITEM = "pi_item";
const NOW = "2026-09-25T12:00:00Z";
const DIGEST = `sha256:${"a".repeat(64)}`;

function review(
  overrides: Partial<KnowledgeReviewDetail> = {},
): KnowledgeReviewDetail {
  return {
    reviewId: "krv_1",
    sourceRevisionIds: [REVISION],
    status: "checking",
    version: 3,
    allowedActions: ["edit", "submit", "read_original"],
    reviewerActorId: null,
    editorActorIds: ["editor"],
    expiresAt: "2027-01-01T00:00:00Z",
    approvalDigest: DIGEST,
    approvedItemIds: [],
    blockingItemIds: [],
    currentPublication: null,
    publicationTarget: {
      status: "unavailable",
      generation: null,
      reason: "review-not-approved",
    },
    inventory: {
      items: [
        {
          itemId: ITEM,
          sourceRevisionId: REVISION,
          kind: "paragraph",
          locator: "page:4:paragraph:2",
          status: "needs_review",
          reason: "check amount",
          attempt: 1,
          artifactDigest: DIGEST,
          decisionActorId: null,
        },
      ],
      nextCursor: null,
      totalCount: 1,
      parsedCount: 0,
      failedCount: 0,
      needsReviewCount: 1,
      excludedCount: 0,
    },
    decisions: [],
    decisionHistory: [],
    decisionHistoryNextCursor: null,
    decisionHistoryTotalCount: 0,
    history: [
      {
        action: "created",
        actorId: "editor",
        occurredAt: NOW,
        reviewVersion: 1,
        itemId: null,
        decisionId: null,
        decisionDigest: null,
      },
    ],
    historyNextCursor: null,
    historyTotalCount: 1,
    publicationIds: [],
    publicationNextCursor: null,
    publicationTotalCount: 0,
    ...overrides,
  };
}

describe("KnowledgeReview", () => {
  it("locates an inventory issue and compares exact extracted content with original availability", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withReviews([review()])
      .withComparison({
        reviewId: "krv_1",
        itemId: ITEM,
        original: {
          availability: "unavailable",
          excerpt: null,
          reason: "item-aligned-original-unavailable",
        },
        extracted: {
          availability: "available",
          excerpt: "Amount is 15%. Exclusions apply.",
          reason: null,
        },
      });
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });

    expect(await screen.findByText(/待核对 1/u)).toBeVisible();
    await user.click(
      screen.getByRole("button", { name: /page:4:paragraph:2/u }),
    );
    const comparison = await screen.findByRole("heading", {
      name: "原件与提取对照",
    });
    expect(comparison).toBeVisible();
    expect(screen.getByText("item-aligned-original-unavailable")).toBeVisible();
    expect(screen.getByText("Amount is 15%. Exclusions apply.")).toBeVisible();
  });

  it("does not expose original text when the server omits read_original", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withReviews([review({ allowedActions: ["edit"] })])
      .withComparison({
        reviewId: "krv_1",
        itemId: ITEM,
        original: {
          availability: "available",
          excerpt: "Private original terms",
          reason: null,
        },
        extracted: {
          availability: "available",
          excerpt: "Extracted terms",
          reason: null,
        },
      });
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: /page:4:paragraph:2/u }),
    );
    expect(await screen.findByText("Extracted terms")).toBeVisible();
    expect(
      screen.queryByText("Private original terms"),
    ).not.toBeInTheDocument();
  });

  it("records an exception decision with the loaded review version", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withReviews([review()])
      .withComparison({
        reviewId: "krv_1",
        itemId: ITEM,
        original: {
          availability: "unavailable",
          excerpt: null,
          reason: "unavailable",
        },
        extracted: {
          availability: "available",
          excerpt: "Exception wording",
          reason: null,
        },
      });
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: /page:4:paragraph:2/u }),
    );
    await screen.findByText("Exception wording");
    await user.click(screen.getByRole("combobox", { name: "核对字段" }));
    await user.click(await screen.findByText("例外条件"));
    await user.type(
      screen.getByRole("textbox", { name: "核对说明" }),
      "Exception missing from extraction",
    );
    await user.click(screen.getByRole("button", { name: "保存核对" }));
    await waitFor(() =>
      expect(api.reviewCommands).toContainEqual({
        action: "edit",
        version: 3,
        itemId: ITEM,
      }),
    );
    expect(
      await screen.findByText(/Exception missing from extraction/u),
    ).toBeVisible();
  });

  it("uses server capabilities for independent review and versioned transitions", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withReviews([
      review({ status: "reviewing", allowedActions: ["return", "approve"] }),
    ]);
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    expect(
      await screen.findByRole("button", { name: "批准审核" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "保存核对" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "发布" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "退回核对" }));
    await waitFor(() =>
      expect(api.reviewCommands).toContainEqual({
        action: "return",
        version: 3,
      }),
    );
  });

  it("publishes only the server target and withdraws with its publication version", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withReviews([
      review({
        status: "approved",
        allowedActions: ["publish", "withdraw"],
        publicationTarget: {
          status: "ready",
          generation: "generation-7",
          reason: null,
        },
      }),
    ]);
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(await screen.findByRole("button", { name: /发\s*布/u }));
    await waitFor(() =>
      expect(api.reviewCommands).toContainEqual({
        action: "publish",
        version: 3,
      }),
    );
    await user.click(await screen.findByRole("button", { name: "撤回发布" }));
    await waitFor(() =>
      expect(api.reviewCommands).toContainEqual({
        action: "withdraw",
        version: 1,
      }),
    );
  });

  it("reloads the server review after remount, preserving revision history", async () => {
    const api = fakeKnowledgeClient().withReviews([review()]);
    const first = renderKnowledgeApp(
      <KnowledgeReview sourceRevisionId={REVISION} />,
      { api },
    );
    expect(await screen.findByText(/created · editor · v1/u)).toBeVisible();
    first.unmount();
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    expect(await screen.findByText(/created · editor · v1/u)).toBeVisible();
    expect(screen.getByText("审核版本 3")).toBeVisible();
  });

  it("discovers later review revisions through the server cursor", async () => {
    const user = userEvent.setup();
    const first = review();
    const later = review({ reviewId: "krv_2", version: 6, status: "approved" });
    const api = fakeKnowledgeClient().withReviews([first, later]);
    api.listReviews = async ({ afterReviewId }) =>
      afterReviewId
        ? { items: [later], nextCursor: null }
        : { items: [first], nextCursor: first.reviewId };
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: "加载更多审核修订" }),
    );
    await user.click(
      await screen.findByRole("combobox", { name: "选择审核修订" }),
    );
    await user.click(await screen.findByText(/krv_2 · 已批准/u));
    expect(await screen.findByText("审核版本 6")).toBeVisible();
  });

  it("loads historical publications after a withdrawal instead of treating them as current", async () => {
    const publication = {
      publicationId: "pub-old",
      reviewId: "krv_1",
      reviewVersion: 4,
      version: 2,
      status: "withdrawn" as const,
      generation: "generation-old",
      approvalDigest: DIGEST,
      sourceRevisionIds: [REVISION],
      approvedItemIds: [ITEM],
      publishedAt: NOW,
      expiresAt: "2027-01-01T00:00:00Z",
      withdrawnAt: NOW,
      withdrawnBy: "publisher",
    };
    const api = fakeKnowledgeClient()
      .withReviews([
        review({
          status: "withdrawn",
          currentPublication: null,
          publicationIds: [publication.publicationId],
          publicationTotalCount: 1,
        }),
      ])
      .withPublicationHistory([publication]);
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    expect(await screen.findByText(/pub-old · 已撤回/u)).toBeVisible();
    expect(screen.getByText(/发布状态：未发布/u)).toBeVisible();
  });

  it("reloads authoritative version after a 409 and keeps the unsubmitted note", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withReviews([review()])
      .withComparison({
        reviewId: "krv_1",
        itemId: ITEM,
        original: {
          availability: "unavailable",
          excerpt: null,
          reason: "unavailable",
        },
        extracted: {
          availability: "available",
          excerpt: "Current extracted text",
          reason: null,
        },
      });
    api.decideReviewItem = async () => {
      api.withReviews([review({ version: 4 })]);
      throw new KnowledgeClientError({
        type: "https://tap.example/problems/revision-conflict",
        title: "Revision conflict",
        status: 409,
        detail: "The requested revision conflicts with the current revision.",
        correlationId: "conflict-1",
        retryable: false,
      });
    };
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: /page:4:paragraph:2/u }),
    );
    await screen.findByText("Current extracted text");
    await user.type(
      screen.getByRole("textbox", { name: "核对说明" }),
      "Check exception wording",
    );
    await user.click(screen.getByRole("button", { name: "保存核对" }));
    expect(await screen.findByText("审核版本 4")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("已重新加载最新记录");
    expect(screen.getByRole("textbox", { name: "核对说明" })).toHaveValue(
      "Check exception wording",
    );
  });

  it("shows a denied command without claiming the review changed", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withReviews([
      review({ allowedActions: ["submit"] }),
    ]);
    api.transitionReview = async () => {
      throw new KnowledgeClientError({
        type: "https://tap.example/problems/authorization-denied",
        title: "Authorization denied",
        status: 403,
        detail: "The current actor and scope do not allow this operation.",
        correlationId: "denied-1",
        retryable: false,
      });
    };
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: "提交独立复核" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("无权执行");
    expect(screen.getByText("审核版本 3")).toBeVisible();
  });
});
