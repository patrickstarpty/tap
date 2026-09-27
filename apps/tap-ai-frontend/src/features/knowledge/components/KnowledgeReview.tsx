import {
  Alert,
  Button,
  Input,
  Popconfirm,
  Select,
  Skeleton,
  Space,
  Tag,
  Typography,
} from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import { KnowledgeClientError } from "../api/client";
import { knowledgeKeys, useKnowledgeClient } from "../api/queries";
import type {
  KnowledgePublicationDetail,
  KnowledgeReviewDecisionRequest,
  KnowledgeReviewDetail,
  KnowledgeReviewItemComparison,
} from "../api/types";

const REVIEW_STATUS: Record<KnowledgeReviewDetail["status"], string> = {
  draft: "草稿",
  checking: "核对中",
  reviewing: "待独立复核",
  approved: "已批准",
  published: "已发布",
  needs_review: "需重新核对",
  expired: "已过期",
  withdrawn: "已撤回",
};
const ITEM_STATUS = {
  parsed: "已提取",
  failed: "提取失败",
  needs_review: "待核对",
  excluded: "已排除",
} as const;
const CHECK_KIND = {
  scope: "适用范围",
  term: "业务条款",
  amount: "金额或比例",
  unit: "单位",
  exception: "例外条件",
} as const;
const DECISION_STATUS = {
  accepted: "核对通过",
  blocked: "存在问题",
  excluded: "人工排除",
} as const;

function errorMessage(error: unknown): string {
  if (error instanceof KnowledgeClientError) {
    if (error.status === 403)
      return "当前账号无权执行此操作，请刷新权限后重试。";
    if (error.status === 409)
      return "审核版本已变化，已重新加载最新记录。请核对后再提交。";
    if (error.code === "knowledge-projection-not-ready")
      return "发布投影尚未就绪，现有发布版本未变化。";
  }
  return "操作未完成，请检查服务状态并重试。";
}

function Preview({
  value,
}: {
  value: KnowledgeReviewItemComparison["original"];
}) {
  if (value.availability !== "available" || !value.excerpt) {
    return <p role="status">{value.reason ?? "该位置暂无可核对内容。"}</p>;
  }
  return <pre className="tapper-preview">{value.excerpt}</pre>;
}

export function KnowledgeReview({
  documentId,
  sourceRevisionId,
  onPublicationChange,
}: {
  documentId?: string;
  sourceRevisionId: string;
  onPublicationChange?: () => void;
}) {
  const client = useKnowledgeClient();
  const queryClient = useQueryClient();
  const [reviews, setReviews] = useState<KnowledgeReviewDetail[]>([]);
  const [reviewCursor, setReviewCursor] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [review, setReview] = useState<KnowledgeReviewDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [writeLocked, setWriteLocked] = useState(false);
  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [comparison, setComparison] =
    useState<KnowledgeReviewItemComparison | null>(null);
  const [checkKind, setCheckKind] =
    useState<KnowledgeReviewDecisionRequest["checkKind"]>("scope");
  const [decisionStatus, setDecisionStatus] =
    useState<KnowledgeReviewDecisionRequest["status"]>("accepted");
  const [note, setNote] = useState("");
  const [inventory, setInventory] = useState<
    KnowledgeReviewDetail["inventory"] | null
  >(null);
  const [history, setHistory] = useState<KnowledgeReviewDetail["history"]>([]);
  const [decisionHistory, setDecisionHistory] = useState<
    KnowledgeReviewDetail["decisionHistory"]
  >([]);
  const [publications, setPublications] = useState<
    KnowledgePublicationDetail[]
  >([]);
  const [historyCursor, setHistoryCursor] = useState<number | null>(null);
  const [decisionCursor, setDecisionCursor] = useState<number | null>(null);
  const [publicationCursor, setPublicationCursor] = useState<string | null>(
    null,
  );
  const itemTrigger = useRef<HTMLElement | null>(null);
  const selectedReview = useRef<string | null>(null);
  const comparisonRequest = useRef(0);
  const openIntent = useRef<string | null>(null);
  const mutationIntents = useRef(new Map<string, string>());
  const openRequest = useRef(0);
  const scope = `${documentId ?? ""}\u0000${sourceRevisionId}`;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const comparisonHeading = useRef<HTMLElement | null>(null);

  const intentFor = (
    action: "publish" | "withdraw",
    id: string,
    version: number,
    generation?: string,
  ): string => {
    const scope = JSON.stringify([action, id, version, generation ?? null]);
    const prior = mutationIntents.current.get(scope);
    if (prior) return prior;
    const intent = `${action}:${crypto.randomUUID()}`;
    mutationIntents.current.set(scope, intent);
    return intent;
  };

  const adopt = useCallback((value: KnowledgeReviewDetail) => {
    setReview(value);
    setReviews((items) =>
      items.map((item) => (item.reviewId === value.reviewId ? value : item)),
    );
    setInventory(value.inventory);
    setHistory(value.history);
    setDecisionHistory(value.decisionHistory);
    setHistoryCursor(value.historyNextCursor ?? null);
    setDecisionCursor(value.decisionHistoryNextCursor ?? null);
    setPublicationCursor(value.publicationNextCursor ?? null);
    setPublications(value.currentPublication ? [value.currentPublication] : []);
  }, []);

  const reload = useCallback(
    async (reviewId: string) => {
      const latest = await client.getReview(reviewId);
      if (selectedReview.current !== reviewId) return;
      const page = await client.listReviewPublications(reviewId);
      if (selectedReview.current !== reviewId) return;
      adopt(latest);
      setPublications(page.items);
      setPublicationCursor(page.nextCursor ?? null);
      setWriteLocked(false);
    },
    [adopt, client],
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setReviews([]);
    setReviewCursor(null);
    selectedReview.current = null;
    comparisonRequest.current += 1;
    setReview(null);
    setSelectedId(null);
    setSelectedItemId(null);
    setComparison(null);
    setNote("");
    setCheckKind("scope");
    setDecisionStatus("accepted");
    openIntent.current = null;
    openRequest.current += 1;
    setPending(false);
    void client
      .listReviews({ sourceRevisionId, limit: 50 })
      .then((page) => {
        if (!active) return;
        setReviews(page.items);
        setReviewCursor(page.nextCursor ?? null);
        const first = page.items[0];
        if (first) {
          selectedReview.current = first.reviewId;
          setSelectedId(first.reviewId);
          adopt(first);
        }
      })
      .catch((failure: unknown) => {
        if (active) setError(errorMessage(failure));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      openRequest.current += 1;
    };
  }, [adopt, client, documentId, sourceRevisionId]);

  const reloadEmpty = async () => {
    const expectedScope = scope;
    const page = await client.listReviews({ sourceRevisionId, limit: 50 });
    if (currentScope.current !== expectedScope) return;
    setReviews(page.items);
    setReviewCursor(page.nextCursor ?? null);
    const first = page.items[0];
    if (first) {
      selectedReview.current = first.reviewId;
      setSelectedId(first.reviewId);
      adopt(first);
    }
  };

  const openReview = async () => {
    if (!documentId || loading || pending || review) return;
    setPending(true);
    setError(null);
    const expectedScope = scope;
    const request = ++openRequest.current;
    const intent = openIntent.current ?? `open-review:${crypto.randomUUID()}`;
    openIntent.current = intent;
    try {
      const opened = await client.openDocumentReview(
        documentId,
        sourceRevisionId,
        intent,
      );
      if (
        currentScope.current !== expectedScope ||
        openRequest.current !== request
      )
        return;
      openIntent.current = null;
      selectedReview.current = opened.reviewId;
      setSelectedId(opened.reviewId);
      setReviews([opened]);
      adopt(opened);
    } catch (failure) {
      if (
        currentScope.current !== expectedScope ||
        openRequest.current !== request
      )
        return;
      if (failure instanceof KnowledgeClientError && failure.status === 409) {
        try {
          await reloadEmpty();
        } catch {
          /* Retain conflict notice and offer a manual reload. */
        }
      }
      setError(errorMessage(failure));
    } finally {
      if (
        currentScope.current === expectedScope &&
        openRequest.current === request
      )
        setPending(false);
    }
  };

  useEffect(() => {
    if (selectedItemId !== null) comparisonHeading.current?.focus();
  }, [selectedItemId]);

  useEffect(() => {
    if (selectedId === null) return;
    let active = true;
    void reload(selectedId)
      .then(() => {
        if (!active) return;
      })
      .catch((failure: unknown) => {
        if (active) setError(errorMessage(failure));
      });
    return () => {
      active = false;
    };
  }, [reload, selectedId]);

  const execute = async (
    work: (current: KnowledgeReviewDetail) => Promise<unknown>,
    publication = false,
  ) => {
    if (!review || pending || writeLocked) return false;
    setPending(true);
    setError(null);
    try {
      try {
        await work(review);
      } catch (failure) {
        if (failure instanceof KnowledgeClientError && failure.status === 409) {
          setWriteLocked(true);
          try {
            await reload(review.reviewId);
          } catch {
            /* Keep the conflict message and require a retry. */
          }
        }
        setError(errorMessage(failure));
        return false;
      }
      setWriteLocked(true);
      if (publication) {
        onPublicationChange?.();
        void queryClient.invalidateQueries({
          queryKey: knowledgeKeys.publishedSources(client.projectId),
        });
      }
      try {
        await reload(review.reviewId);
        return true;
      } catch {
        setError("操作已提交，但最新状态读取失败。请重新加载后再操作。");
        return true;
      }
    } finally {
      setPending(false);
    }
  };

  const openItem = async (itemId: string, trigger: HTMLElement) => {
    if (!review) return;
    const request = ++comparisonRequest.current;
    const reviewId = review.reviewId;
    itemTrigger.current = trigger;
    if (selectedItemId !== itemId) {
      setNote("");
      setCheckKind("scope");
      setDecisionStatus("accepted");
    }
    setSelectedItemId(itemId);
    setComparison(null);
    setError(null);
    try {
      const value = await client.compareReviewItem(reviewId, itemId);
      if (
        comparisonRequest.current === request &&
        selectedReview.current === reviewId &&
        value.itemId === itemId
      )
        setComparison(value);
    } catch (failure) {
      if (
        comparisonRequest.current === request &&
        selectedReview.current === reviewId
      )
        setError(errorMessage(failure));
    }
  };

  const closeItem = () => {
    comparisonRequest.current += 1;
    setSelectedItemId(null);
    setComparison(null);
    queueMicrotask(() => itemTrigger.current?.focus());
  };

  const loadMore = async (
    kind: "inventory" | "history" | "decisions" | "publications",
  ) => {
    if (!review || pending) return;
    setPending(true);
    setError(null);
    try {
      if (kind === "inventory" && inventory?.nextCursor) {
        const page = await client.listReviewInventory(
          review.reviewId,
          inventory.nextCursor,
        );
        setInventory({ ...page, items: [...inventory.items, ...page.items] });
      }
      if (kind === "history" && historyCursor !== null) {
        const page = await client.listReviewHistory(
          review.reviewId,
          historyCursor,
        );
        setHistory((items) => [...items, ...page.items]);
        setHistoryCursor(page.nextCursor ?? null);
      }
      if (kind === "decisions" && decisionCursor !== null) {
        const page = await client.listReviewDecisionHistory(
          review.reviewId,
          decisionCursor,
        );
        setDecisionHistory((items) => [...items, ...page.items]);
        setDecisionCursor(page.nextCursor ?? null);
      }
      if (kind === "publications" && publicationCursor !== null) {
        const page = await client.listReviewPublications(
          review.reviewId,
          publicationCursor,
        );
        setPublications((items) => [
          ...items,
          ...page.items.filter(
            (value) =>
              !items.some((item) => item.publicationId === value.publicationId),
          ),
        ]);
        setPublicationCursor(page.nextCursor ?? null);
      }
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setPending(false);
    }
  };

  const loadMoreReviews = async () => {
    if (reviewCursor === null || pending) return;
    setPending(true);
    setError(null);
    try {
      const page = await client.listReviews({
        sourceRevisionId,
        afterReviewId: reviewCursor,
        limit: 50,
      });
      setReviews((items) => [
        ...items,
        ...page.items.filter(
          (value) => !items.some((item) => item.reviewId === value.reviewId),
        ),
      ]);
      setReviewCursor(page.nextCursor ?? null);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setPending(false);
    }
  };

  return (
    <section
      aria-labelledby="knowledge-review-heading"
      className="tapper-review"
    >
      <Typography.Title level={5} id="knowledge-review-heading">
        业务审核
      </Typography.Title>
      {loading ? <Skeleton active paragraph={{ rows: 3 }} /> : null}
      {error ? (
        <Alert
          type="error"
          showIcon
          title={error}
          action={
            review ? (
              <Button
                size="small"
                onClick={() =>
                  void reload(review.reviewId)
                    .then(() => setError(null))
                    .catch((failure: unknown) =>
                      setError(errorMessage(failure)),
                    )
                }
              >
                重新加载
              </Button>
            ) : (
              <Button
                size="small"
                loading={pending}
                onClick={() =>
                  void reloadEmpty()
                    .then(() => setError(null))
                    .catch((failure: unknown) =>
                      setError(errorMessage(failure)),
                    )
                }
              >
                重新加载
              </Button>
            )
          }
        />
      ) : null}
      {!loading && reviews.length === 0 ? (
        <div>
          <p>
            <strong>未关联审核记录</strong>
            。上传或解析就绪不代表已通过业务审核。
          </p>
          {documentId ? (
            <Button
              type="primary"
              loading={pending}
              onClick={() => void openReview()}
            >
              开始业务审核
            </Button>
          ) : null}
        </div>
      ) : null}
      {reviews.length > 1 ? (
        <Select
          aria-label="选择审核修订"
          disabled={pending}
          value={selectedId}
          onChange={(id) => {
            selectedReview.current = id;
            comparisonRequest.current += 1;
            setSelectedItemId(null);
            setComparison(null);
            setNote("");
            setCheckKind("scope");
            setDecisionStatus("accepted");
            setReview(null);
            setSelectedId(id);
          }}
          options={reviews.map((item) => ({
            value: item.reviewId,
            label: `${item.reviewId} · ${REVIEW_STATUS[item.status]}`,
          }))}
        />
      ) : null}
      {reviewCursor !== null ? (
        <Button loading={pending} onClick={() => void loadMoreReviews()}>
          加载更多审核修订
        </Button>
      ) : null}
      {review ? (
        <>
          <p>
            <Tag>{REVIEW_STATUS[review.status]}</Tag>
            <span>审核版本 {review.version}</span>
          </p>
          <p>
            复核人：{review.reviewerActorId ?? "尚未复核"} · 有效期：
            {review.expiresAt}
          </p>
          <p>
            发布状态：
            {review.currentPublication?.status === "published"
              ? "已发布"
              : "未发布"}{" "}
            · 投影：
            {review.publicationTarget.status === "ready"
              ? "就绪"
              : (review.publicationTarget.reason ?? "未就绪")}
          </p>
          {inventory ? (
            <section aria-labelledby="review-inventory-heading">
              <Typography.Title level={5} id="review-inventory-heading">
                资料完整性清单
              </Typography.Title>
              <p>{`已提取 ${inventory.parsedCount} · 失败 ${inventory.failedCount} · 待核对 ${inventory.needsReviewCount} · 已排除 ${inventory.excludedCount}`}</p>
              {inventory.totalCount === 0 ? (
                <p>尚无清单数据，不能提交审核。</p>
              ) : null}
              <ul className="tapper-review-list">
                {!review.allowedActions.includes("read_original") ? (
                  <li>当前账号无权查看原件与提取对照。</li>
                ) : null}
                {inventory.items.map((item) => (
                  <li key={item.itemId}>
                    <Button
                      type="link"
                      disabled={
                        !review.allowedActions.includes("read_original")
                      }
                      onClick={(event) =>
                        void openItem(item.itemId, event.currentTarget)
                      }
                    >
                      {item.kind} · {item.locator}
                    </Button>
                    <span>
                      {ITEM_STATUS[item.status]}
                      {item.reason ? ` · ${item.reason}` : ""}
                    </span>
                    {review.blockingItemIds.includes(item.itemId) ? (
                      <Tag color="error">阻断</Tag>
                    ) : null}
                  </li>
                ))}
              </ul>
              {inventory.nextCursor ? (
                <Button
                  loading={pending}
                  onClick={() => void loadMore("inventory")}
                >
                  加载更多清单
                </Button>
              ) : null}
            </section>
          ) : null}
          {selectedItemId ? (
            <section
              aria-labelledby="review-comparison-heading"
              className="tapper-review-comparison"
            >
              <Space className="tapper-review-heading">
                <Typography.Title
                  level={5}
                  id="review-comparison-heading"
                  tabIndex={-1}
                  ref={comparisonHeading}
                >
                  原件与提取对照
                </Typography.Title>
                <Button onClick={closeItem}>关闭对照</Button>
              </Space>
              <p role="status">
                {comparison
                  ? "对照已加载。"
                  : error
                    ? "对照读取失败。"
                    : "正在读取对照…"}
              </p>
              {!comparison ? (
                <Skeleton active paragraph={{ rows: 3 }} />
              ) : (
                <div className="tapper-review-columns">
                  <article>
                    <h4>原件位置</h4>
                    {review.allowedActions.includes("read_original") ? (
                      <Preview value={comparison.original} />
                    ) : (
                      <p role="status">当前账号无权查看原件。</p>
                    )}
                  </article>
                  <article>
                    <h4>提取内容</h4>
                    <Preview value={comparison.extracted} />
                  </article>
                </div>
              )}
              {review.allowedActions.includes("edit") ? (
                <div className="tapper-review-decision">
                  <Select
                    aria-label="核对字段"
                    value={checkKind}
                    onChange={setCheckKind}
                    options={Object.entries(CHECK_KIND).map(
                      ([value, label]) => ({ value, label }),
                    )}
                  />
                  <Select
                    aria-label="核对结果"
                    value={decisionStatus}
                    onChange={setDecisionStatus}
                    options={Object.entries(DECISION_STATUS).map(
                      ([value, label]) => ({ value, label }),
                    )}
                  />
                  <Input.TextArea
                    aria-label="核对说明"
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    maxLength={2000}
                  />
                  <Button
                    type="primary"
                    disabled={
                      writeLocked || comparison === null || !note.trim()
                    }
                    loading={pending}
                    onClick={() =>
                      void execute((value) =>
                        client.decideReviewItem(
                          value.reviewId,
                          selectedItemId,
                          value.version,
                          {
                            checkKind,
                            status: decisionStatus,
                            note: note.trim(),
                          },
                        ),
                      ).then((saved) => {
                        if (saved) setNote("");
                      })
                    }
                  >
                    保存核对
                  </Button>
                </div>
              ) : null}
            </section>
          ) : null}
          <section aria-labelledby="review-actions-heading">
            <Typography.Title level={5} id="review-actions-heading">
              审核操作
            </Typography.Title>
            <Space wrap>
              {review.allowedActions.includes("submit") ? (
                <Button
                  loading={pending}
                  disabled={writeLocked}
                  onClick={() =>
                    void execute((value) =>
                      client.transitionReview(
                        value.reviewId,
                        "submit",
                        value.version,
                      ),
                    )
                  }
                >
                  提交独立复核
                </Button>
              ) : null}
              {review.allowedActions.includes("return") ? (
                <Button
                  loading={pending}
                  disabled={writeLocked}
                  onClick={() =>
                    void execute((value) =>
                      client.transitionReview(
                        value.reviewId,
                        "return",
                        value.version,
                      ),
                    )
                  }
                >
                  退回核对
                </Button>
              ) : null}
              {review.allowedActions.includes("approve") ? (
                <Button
                  loading={pending}
                  disabled={writeLocked}
                  onClick={() =>
                    void execute((value) =>
                      client.transitionReview(
                        value.reviewId,
                        "approve",
                        value.version,
                      ),
                    )
                  }
                >
                  批准审核
                </Button>
              ) : null}
              {review.allowedActions.includes("publish") ? (
                <Button
                  type="primary"
                  loading={pending}
                  disabled={
                    writeLocked ||
                    review.publicationTarget.status !== "ready" ||
                    !review.publicationTarget.generation
                  }
                  onClick={() =>
                    void execute(
                      (value) =>
                        client.publishReview(
                          value.reviewId,
                          value.version,
                          value.publicationTarget.generation!,
                          intentFor(
                            "publish",
                            value.reviewId,
                            value.version,
                            value.publicationTarget.generation!,
                          ),
                        ),
                      true,
                    )
                  }
                >
                  发布
                </Button>
              ) : null}
              {review.allowedActions.includes("withdraw") &&
              review.currentPublication ? (
                <Popconfirm
                  title="撤回当前发布？"
                  description="撤回后，该来源不再可用于新的正式问答。"
                  okText="确认撤回"
                  cancelText="取消"
                  onConfirm={() =>
                    void execute(
                      (value) =>
                        client.withdrawPublication(
                          value.currentPublication!.publicationId,
                          value.currentPublication!.version,
                          intentFor(
                            "withdraw",
                            value.currentPublication!.publicationId,
                            value.currentPublication!.version,
                          ),
                        ),
                      true,
                    )
                  }
                >
                  <Button danger loading={pending} disabled={writeLocked}>
                    撤回发布
                  </Button>
                </Popconfirm>
              ) : null}
            </Space>
          </section>
          <section aria-labelledby="review-history-heading">
            <Typography.Title level={5} id="review-history-heading">
              修订记录
            </Typography.Title>
            {history.length === 0 ? (
              <p>暂无修订记录。</p>
            ) : (
              <ol>
                {history.map((item) => (
                  <li key={`${item.reviewVersion}-${item.action}`}>
                    {item.action} · {item.actorId} · v{item.reviewVersion} ·{" "}
                    {item.occurredAt}
                  </li>
                ))}
              </ol>
            )}
            {historyCursor !== null ? (
              <Button
                loading={pending}
                onClick={() => void loadMore("history")}
              >
                加载更多修订
              </Button>
            ) : null}
          </section>
          <section aria-labelledby="review-decisions-heading">
            <Typography.Title level={5} id="review-decisions-heading">
              核对记录
            </Typography.Title>
            {decisionHistory.length === 0 ? (
              <p>暂无核对记录。</p>
            ) : (
              <ol>
                {decisionHistory.map((item) => (
                  <li key={item.decisionId}>
                    {CHECK_KIND[item.checkKind]} ·{" "}
                    {DECISION_STATUS[item.status]} · {item.note} ·{" "}
                    {item.actorId}
                  </li>
                ))}
              </ol>
            )}
            {decisionCursor !== null ? (
              <Button
                loading={pending}
                onClick={() => void loadMore("decisions")}
              >
                加载更多核对
              </Button>
            ) : null}
          </section>
          <section aria-labelledby="review-publications-heading">
            <Typography.Title level={5} id="review-publications-heading">
              发布记录
            </Typography.Title>
            {publications.length === 0 ? (
              <p>尚未发布。</p>
            ) : (
              <ol>
                {publications.map((item) => (
                  <li key={item.publicationId}>
                    {item.publicationId} ·{" "}
                    {item.status === "published" ? "已发布" : "已撤回"} ·{" "}
                    {item.publishedAt}
                  </li>
                ))}
              </ol>
            )}
            {publicationCursor !== null ? (
              <Button
                loading={pending}
                onClick={() => void loadMore("publications")}
              >
                加载更多发布
              </Button>
            ) : null}
          </section>
        </>
      ) : null}
    </section>
  );
}
