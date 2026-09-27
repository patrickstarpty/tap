import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type {
  KnowledgeReviewDetail,
  KnowledgeReviewItemComparison,
} from "../api/types";
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
  it("shows the authorized original image beside a flow node extraction", async () => {
    const user = userEvent.setup();
    const imageReview = review({
      inventory: {
        ...review().inventory,
        items: [
          {
            ...review().inventory.items[0]!,
            kind: "flow_node",
            locator: "image:1/node:start",
          },
        ],
      },
    });
    const api = fakeKnowledgeClient()
      .withReviews([imageReview])
      .withComparison({
        reviewId: "krv_1",
        itemId: ITEM,
        original: {
          availability: "unsupported",
          excerpt: null,
          reason: "image-region-not-text",
        },
        extracted: {
          availability: "available",
          excerpt: "流程图节点：开始",
          reason: null,
        },
      });
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });

    await user.click(
      await screen.findByRole("button", { name: /image:1\/node:start/u }),
    );

    expect(
      await screen.findByRole("img", { name: "待核对的流程图原图" }),
    ).toHaveAttribute(
      "src",
      `/api/v1/projects/${api.projectId}/knowledge/reviews/krv_1/items/${ITEM}/original-image`,
    );
    expect(screen.getByText("流程图节点：开始")).toBeVisible();
  });

  it("offers correction or exclusion for an uncertain connection", async () => {
    const user = userEvent.setup();
    const uncertain = review({
      inventory: {
        ...review().inventory,
        items: [
          {
            ...review().inventory.items[0]!,
            kind: "flow_edge",
            locator: "image:1/edge:1",
            reason: "uncertain-connection",
          },
        ],
      },
    });
    const api = fakeKnowledgeClient()
      .withReviews([uncertain])
      .withComparison({
        reviewId: "krv_1",
        itemId: ITEM,
        original: {
          availability: "unsupported",
          excerpt: null,
          reason: "image-region-not-text",
        },
        extracted: {
          availability: "unavailable",
          excerpt: null,
          reason: "not-extracted",
        },
      });
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });

    await user.click(
      await screen.findByRole("button", { name: /image:1\/edge:1/u }),
    );

    expect(
      await screen.findByText(
        "这条连线的方向或端点尚未确认，请编辑流程图更正，或排除此项。",
      ),
    ).toBeVisible();
  });

  it("opens the server review from an empty revision and restores it after remount", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withOpenReview(review({ version: 1 }));
    const view = renderKnowledgeApp(
      <KnowledgeReview documentId="doc_1" sourceRevisionId={REVISION} />,
      { api },
    );
    await user.click(
      await screen.findByRole("button", { name: "开始业务审核" }),
    );
    expect(api.openReviewCalls).toHaveLength(1);
    expect(api.openReviewCalls[0]).toMatchObject({
      documentId: "doc_1",
      sourceRevisionId: REVISION,
    });
    expect(api.openReviewCalls[0]?.key).toMatch(/^open-review:/u);
    expect(await screen.findByText("审核版本 1")).toBeVisible();
    view.unmount();
    renderKnowledgeApp(
      <KnowledgeReview documentId="doc_1" sourceRevisionId={REVISION} />,
      { api },
    );
    expect(await screen.findByText("审核版本 1")).toBeVisible();
  });

  it("keeps an empty review on permission denial", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient();
    api.openDocumentReview = async () => {
      throw new KnowledgeClientError({
        type: "https://tap.example/problems/authorization-denied",
        title: "Authorization denied",
        status: 403,
        detail: "The current actor and scope do not allow this operation.",
        correlationId: "denied-open",
        retryable: false,
      });
    };
    renderKnowledgeApp(
      <KnowledgeReview documentId="doc_1" sourceRevisionId={REVISION} />,
      { api },
    );
    await user.click(
      await screen.findByRole("button", { name: "开始业务审核" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("无权执行");
    expect(screen.queryByText(/审核版本/u)).not.toBeInTheDocument();
  });

  it("retries an uncertain open with the same idempotency key", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withOpenReview(review({ version: 1 }));
    const open = api.openDocumentReview;
    let attempts = 0;
    api.openDocumentReview = async (...args) => {
      attempts += 1;
      if (attempts === 1) {
        api.openReviewCalls.push({
          documentId: args[0],
          sourceRevisionId: args[1],
          key: args[2],
        });
        throw new Error("network interrupted");
      }
      return open(...args);
    };
    renderKnowledgeApp(
      <KnowledgeReview documentId="doc_1" sourceRevisionId={REVISION} />,
      { api },
    );
    await user.click(
      await screen.findByRole("button", { name: "开始业务审核" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("操作未完成");
    await user.click(screen.getByRole("button", { name: "开始业务审核" }));
    expect(await screen.findByText("审核版本 1")).toBeVisible();
    expect(api.openReviewCalls[0]?.key).toBe(api.openReviewCalls[1]?.key);
  });

  it("states an unsupported original without substituting extraction text", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withReviews([review()])
      .withComparison({
        reviewId: "krv_1",
        itemId: ITEM,
        original: {
          availability: "unsupported",
          excerpt: null,
          reason: "pdf-compressed-stream",
        },
        extracted: {
          availability: "available",
          excerpt: "Extracted policy",
          reason: null,
        },
      });
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: /page:4:paragraph:2/u }),
    );
    expect(await screen.findByText("pdf-compressed-stream")).toBeVisible();
    expect(screen.getByText("Extracted policy")).toBeVisible();
    expect(screen.queryAllByText("Extracted policy")).toHaveLength(1);
  });

  it("keeps item decisions disabled until the comparison succeeds", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withReviews([review()]);
    let finish!: (value: KnowledgeReviewItemComparison) => void;
    api.compareReviewItem = () =>
      new Promise((resolve) => {
        finish = resolve;
      });
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: /page:4:paragraph:2/u }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "核对说明" }),
      "Evidence checked",
    );
    expect(screen.getByRole("button", { name: "保存核对" })).toBeDisabled();
    await act(async () =>
      finish({
        reviewId: "krv_1",
        itemId: ITEM,
        original: {
          availability: "unavailable",
          excerpt: null,
          reason: "original-not-available",
        },
        extracted: {
          availability: "available",
          excerpt: "Extracted evidence",
          reason: null,
        },
      }),
    );
    expect(await screen.findByText("Extracted evidence")).toBeVisible();
    expect(screen.getByRole("button", { name: "保存核对" })).toBeEnabled();
  });

  it("keeps item decisions disabled after comparison read failure", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withReviews([review()]);
    api.compareReviewItem = async () => {
      throw new Error("comparison unavailable");
    };
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: /page:4:paragraph:2/u }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "核对说明" }),
      "Evidence checked",
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("操作未完成");
    expect(screen.getByRole("button", { name: "保存核对" })).toBeDisabled();
    expect(screen.getByText("对照读取失败。")).toBeVisible();
  });

  it("reloads an existing review after an open conflict", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient();
    api.openDocumentReview = async () => {
      api.withReviews([review({ version: 4 })]);
      throw new KnowledgeClientError({
        type: "https://tap.example/problems/revision-conflict",
        title: "Revision conflict",
        status: 409,
        detail: "The requested revision conflicts with the current revision.",
        correlationId: "conflict-open",
        retryable: false,
      });
    };
    renderKnowledgeApp(
      <KnowledgeReview documentId="doc_1" sourceRevisionId={REVISION} />,
      { api },
    );
    await user.click(
      await screen.findByRole("button", { name: "开始业务审核" }),
    );
    expect(await screen.findByText("审核版本 4")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("已重新加载");
  });

  it("ignores an open response for a document that is no longer selected", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient();
    let finish!: (value: KnowledgeReviewDetail) => void;
    api.openDocumentReview = () =>
      new Promise((resolve) => {
        finish = resolve;
      });
    const view = renderKnowledgeApp(
      <KnowledgeReview documentId="doc_1" sourceRevisionId={REVISION} />,
      { api },
    );
    await user.click(
      await screen.findByRole("button", { name: "开始业务审核" }),
    );
    view.rerender(
      <KnowledgeReview documentId="doc_2" sourceRevisionId="rev_2" />,
    );
    await act(async () => finish(review({ version: 4 })));
    expect(screen.queryByText("审核版本 4")).not.toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "开始业务审核" }),
    ).toBeEnabled();
  });

  it("clears a draft note when switching item and focuses the opened comparison", async () => {
    const user = userEvent.setup();
    const first = review();
    const otherItem = {
      ...first.inventory.items[0]!,
      itemId: "pi_other",
      locator: "page:5:paragraph:1",
    };
    const api = fakeKnowledgeClient().withReviews([
      review({
        inventory: {
          ...first.inventory,
          items: [...first.inventory.items, otherItem],
          totalCount: 2,
        },
      }),
    ]);
    for (const item of [first.inventory.items[0]!, otherItem]) {
      api.withComparison({
        reviewId: "krv_1",
        itemId: item.itemId,
        original: {
          availability: "available",
          excerpt: "Original policy",
          reason: null,
        },
        extracted: {
          availability: "available",
          excerpt: "Extracted policy",
          reason: null,
        },
      });
    }
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: /page:4:paragraph:2/u }),
    );
    expect(
      await screen.findByRole("heading", { name: "原件与提取对照" }),
    ).toHaveFocus();
    await user.type(
      screen.getByRole("textbox", { name: "核对说明" }),
      "Note for first item",
    );
    await user.click(
      screen.getByRole("button", { name: /page:5:paragraph:1/u }),
    );
    expect(screen.getByRole("textbox", { name: "核对说明" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "保存核对" })).toBeDisabled();
  });

  it("shows item-aligned original and extraction together when both are available", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withReviews([review()])
      .withComparison({
        reviewId: "krv_1",
        itemId: ITEM,
        original: {
          availability: "available",
          excerpt: "Original policy",
          reason: null,
        },
        extracted: {
          availability: "available",
          excerpt: "Extracted policy",
          reason: null,
        },
      });
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: /page:4:paragraph:2/u }),
    );
    expect(await screen.findByText("Original policy")).toBeVisible();
    expect(screen.getByText("Extracted policy")).toBeVisible();
  });
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

  it("does not offer comparison when the server omits read_original", async () => {
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
    const item = await screen.findByRole("button", {
      name: /page:4:paragraph:2/u,
    });
    expect(item).toBeDisabled();
    expect(screen.getByText("当前账号无权查看原件与提取对照。")).toBeVisible();
    expect(
      screen.queryByText("Private original terms"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Extracted terms")).not.toBeInTheDocument();
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
    expect(api.reviewCommands).not.toContainEqual({
      action: "withdraw",
      version: 1,
    });
    await user.click(await screen.findByRole("button", { name: "确认撤回" }));
    await waitFor(() =>
      expect(api.reviewCommands).toContainEqual({
        action: "withdraw",
        version: 1,
      }),
    );
  });

  it("reuses publication intent keys after uncertain publish and withdraw responses", async () => {
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
    const publish = api.publishReview;
    const publishKeys: Array<string | undefined> = [];
    api.publishReview = async (...args) => {
      publishKeys.push(args[3]);
      if (publishKeys.length === 1) throw new Error("response lost");
      return publish(...args);
    };
    const withdraw = api.withdrawPublication;
    const withdrawKeys: Array<string | undefined> = [];
    api.withdrawPublication = async (...args) => {
      withdrawKeys.push(args[2]);
      if (withdrawKeys.length === 1) throw new Error("response lost");
      return withdraw(...args);
    };
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(await screen.findByRole("button", { name: /发\s*布/u }));
    expect(await screen.findByRole("alert")).toHaveTextContent("操作未完成");
    await user.click(screen.getByRole("button", { name: /发\s*布/u }));
    expect(await screen.findByText(/发布状态：已发布/u)).toBeVisible();
    expect(publishKeys[0]).toMatch(/^publish:/u);
    expect(publishKeys[0]).toBe(publishKeys[1]);
    await user.click(screen.getByRole("button", { name: "撤回发布" }));
    await user.click(screen.getByRole("button", { name: "确认撤回" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("操作未完成");
    await user.click(screen.getByRole("button", { name: "撤回发布" }));
    await user.click(screen.getByRole("button", { name: "确认撤回" }));
    expect(await screen.findByText(/发布状态：未发布/u)).toBeVisible();
    expect(withdrawKeys[0]).toMatch(/^withdraw:/u);
    expect(withdrawKeys[0]).toBe(withdrawKeys[1]);
  });

  it("treats a committed publication as committed even when follow-up reload fails", async () => {
    const user = userEvent.setup();
    const onPublicationChange = vi.fn();
    const api = fakeKnowledgeClient().withReviews([
      review({
        status: "approved",
        allowedActions: ["publish"],
        publicationTarget: {
          status: "ready",
          generation: "generation-7",
          reason: null,
        },
      }),
    ]);
    const publish = api.publishReview.bind(api);
    api.publishReview = async (...args) => {
      const result = await publish(...args);
      api.getReview = async () => {
        throw new Error("refresh unavailable");
      };
      return result;
    };
    renderKnowledgeApp(
      <KnowledgeReview
        sourceRevisionId={REVISION}
        onPublicationChange={onPublicationChange}
      />,
      { api },
    );
    await user.click(await screen.findByRole("button", { name: /发\s*布/u }));
    await waitFor(() => expect(onPublicationChange).toHaveBeenCalledOnce());
    expect(screen.getByRole("alert")).toHaveTextContent("已提交");
    expect(screen.getByRole("alert")).not.toHaveTextContent("操作未完成");
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

  it("does not let a slower prior review replace the current selection", async () => {
    const user = userEvent.setup();
    const first = review();
    const later = review({ reviewId: "krv_2", version: 6, status: "approved" });
    const api = fakeKnowledgeClient().withReviews([first, later]);
    const getReview = api.getReview.bind(api);
    let resolveLater: (value: KnowledgeReviewDetail) => void = () => undefined;
    api.getReview = async (id) =>
      id === later.reviewId
        ? new Promise((resolve) => {
            resolveLater = resolve;
          })
        : getReview(id);
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    const chooser = await screen.findByRole("combobox", {
      name: "选择审核修订",
    });
    await user.click(chooser);
    await user.click(await screen.findByText(/krv_2 · 已批准/u));
    await user.click(chooser);
    await user.click(await screen.findByText(/krv_1 · 核对中/u));
    await act(async () => resolveLater(later));
    expect(screen.getByText("审核版本 3")).toBeVisible();
    expect(screen.queryByText("审核版本 6")).not.toBeInTheDocument();
  });

  it("ignores an older comparison response after another item is selected", async () => {
    const user = userEvent.setup();
    const first = review();
    const secondItem = {
      ...first.inventory.items[0]!,
      itemId: "pi_second",
      locator: "page:5:paragraph:1",
    };
    const api = fakeKnowledgeClient().withReviews([
      review({
        inventory: {
          ...first.inventory,
          items: [...first.inventory.items, secondItem],
          totalCount: 2,
          needsReviewCount: 2,
        },
      }),
    ]);
    const pending = new Map<
      string,
      (value: KnowledgeReviewItemComparison) => void
    >();
    api.compareReviewItem = async (_reviewId, itemId) =>
      new Promise((resolve) => {
        pending.set(itemId, resolve);
      });
    renderKnowledgeApp(<KnowledgeReview sourceRevisionId={REVISION} />, {
      api,
    });
    await user.click(
      await screen.findByRole("button", { name: /page:4:paragraph:2/u }),
    );
    await user.click(
      screen.getByRole("button", { name: /page:5:paragraph:1/u }),
    );
    const comparison = (
      itemId: string,
      excerpt: string,
    ): KnowledgeReviewItemComparison => ({
      reviewId: "krv_1",
      itemId,
      original: {
        availability: "unavailable",
        excerpt: null,
        reason: "unavailable",
      },
      extracted: { availability: "available", excerpt, reason: null },
    });
    await act(async () =>
      pending.get("pi_second")!(comparison("pi_second", "Second content")),
    );
    await act(async () =>
      pending.get(ITEM)!(comparison(ITEM, "First content")),
    );
    expect(screen.getByText("Second content")).toBeVisible();
    expect(screen.queryByText("First content")).not.toBeInTheDocument();
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
