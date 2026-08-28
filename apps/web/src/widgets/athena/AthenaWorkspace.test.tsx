// @ts-expect-error Vitest runs in Node; the browser package intentionally omits Node globals.
import { readFileSync } from "node:fs";
// @ts-expect-error Vitest runs in Node; the browser package intentionally omits Node globals.
import { resolve } from "node:path";

import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { KnowledgeClientError } from "../../features/knowledge/api/client";
import { knowledgeKeys } from "../../features/knowledge/api/queries";
import type {
  DocumentPage,
  RetrievalAnswerResponse,
} from "../../features/knowledge/api/types";
import { GroundedAnswer } from "../../features/knowledge/components/GroundedAnswer";
import {
  answerResponse,
  citationPreview,
  document,
  fakeKnowledgeClient,
  retrievalCitation,
} from "../../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../../features/knowledge/testing/renderKnowledgeApp";
import { AthenaWorkspace } from "./AthenaWorkspace";

const workspaceStyles = readFileSync(resolve("src/app/styles.css"), "utf8");

function readyDocument(documentId: string, filename = `${documentId}.md`) {
  return document({
    documentId,
    filename,
    status: "ready",
    stage: "ready",
  });
}

async function selectSource(
  user: ReturnType<typeof userEvent.setup>,
  name: RegExp,
) {
  await user.click(await screen.findByRole("checkbox", { name }));
}

async function ask(user: ReturnType<typeof userEvent.setup>, question: string) {
  const textbox = screen.getByRole("textbox", { name: "输入问题" });
  await user.clear(textbox);
  await user.type(textbox, question);
  await user.click(screen.getByRole("button", { name: "提问" }));
}

describe("AthenaWorkspace source selection", () => {
  it("starts empty, enables only ready sources, and never submits without a source", async () => {
    const api = fakeKnowledgeClient().withDocuments([
      readyDocument("ready", "same.md"),
      document({
        documentId: "queued",
        filename: "same.md",
        status: "queued",
      }),
      document({
        documentId: "processing",
        filename: "processing.md",
        status: "processing",
        stage: "embedding",
      }),
      document({
        documentId: "failed",
        filename: "failed.md",
        status: "failed",
        stage: "publishing",
      }),
      document({
        documentId: "deleting",
        filename: "deleting.md",
        status: "deleting",
        stage: "ready",
      }),
    ]);
    renderKnowledgeApp(<AthenaWorkspace />, { api });

    expect(await screen.findByText("已选择 0 个来源")).toBeVisible();
    expect(
      await screen.findByRole("checkbox", { name: /same\.md.*ready/u }),
    ).toBeEnabled();
    for (const id of ["queued", "processing", "failed", "deleting"]) {
      expect(
        screen.getByRole("checkbox", { name: new RegExp(id, "u") }),
      ).toBeDisabled();
    }
    expect(screen.getByRole("button", { name: "提问" })).toBeDisabled();
    expect(api.answerCalls).toHaveLength(0);
  });

  it("selects only the first twenty sorted ready IDs and blocks the twenty-first", async () => {
    const user = userEvent.setup();
    const documents = Array.from({ length: 21 }, (_, index) => {
      const id = `doc-${String(21 - index).padStart(2, "0")}`;
      return readyDocument(id);
    });
    const api = fakeKnowledgeClient().withDocuments(documents);
    renderKnowledgeApp(<AthenaWorkspace />, { api });

    await user.click(
      await screen.findByRole("button", {
        name: "选择全部已就绪来源",
      }),
    );

    expect(screen.getByText("已选择 20 个来源")).toBeVisible();
    const twentyFirst = screen.getByRole("checkbox", { name: /doc-21/u });
    expect(twentyFirst).not.toBeChecked();
    expect(twentyFirst).toBeDisabled();
    expect(screen.getByText("一次最多选择 20 个来源。")).toBeVisible();
  });

  it("filters display without mutating selection and disambiguates duplicate filenames", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withDocuments([
      readyDocument("doc-a", "shared.md"),
      readyDocument("doc-b", "shared.md"),
    ]);
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /shared\.md.*doc-a/u);

    await user.type(
      screen.getByRole("searchbox", { name: "搜索来源" }),
      "doc-b",
    );

    expect(screen.getByText("已选择 1 个来源")).toBeVisible();
    expect(
      screen.queryByRole("checkbox", { name: /shared\.md.*doc-a/u }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /shared\.md.*doc-b/u }),
    ).toBeInTheDocument();
  });

  it("renders truthful pending, error, and empty source states", async () => {
    const pending = fakeKnowledgeClient().deferList();
    const pendingRender = renderKnowledgeApp(<AthenaWorkspace />, {
      api: pending,
    });
    expect(await screen.findByLabelText("正在加载来源")).toBeVisible();
    pending.finishList();
    pendingRender.unmount();

    const problem = new KnowledgeClientError({
      type: "https://tap.example/problems/search-unavailable",
      title: "provider secret",
      status: 503,
      detail: "provider secret",
    });
    const failedRender = renderKnowledgeApp(<AthenaWorkspace />, {
      api: fakeKnowledgeClient().withListProblem(problem),
    });
    expect(
      await screen.findByText("暂时无法加载来源，请稍后重试。"),
    ).toBeVisible();
    expect(screen.queryByText("还没有可用来源")).not.toBeInTheDocument();
    failedRender.unmount();

    renderKnowledgeApp(<AthenaWorkspace />, { api: fakeKnowledgeClient() });
    expect(await screen.findByText("还没有可用来源")).toBeVisible();
  });
});

describe("AthenaWorkspace answer lifecycle", () => {
  it("sends the exact trimmed quick/doc/scope request with no hidden controls", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withDocuments([
      readyDocument("doc-b"),
      readyDocument("doc-a"),
    ]);
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-b/u);
    await selectSource(user, /doc-a/u);

    await ask(user, " 退款需要几人审批？ ");

    await waitFor(() => expect(api.answerCalls).toHaveLength(1));
    expect(api.answerCalls[0]).toStrictEqual({
      query: "退款需要几人审批？",
      answerMode: "quick",
      sources: ["doc"],
      resourceRefs: [
        { family: "doc", sourceId: "doc-a", mode: "scope" },
        { family: "doc", sourceId: "doc-b", mode: "scope" },
      ],
    });
    expect(Object.keys(api.answerCalls[0] ?? {}).sort()).toEqual([
      "answerMode",
      "query",
      "resourceRefs",
      "sources",
    ]);
  });

  it("submits once on plain Enter without adding a newline", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a")])
      .deferAnswer();
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    const textbox = screen.getByRole("textbox", { name: "输入问题" });
    await user.type(textbox, "退款规则是什么？");

    fireEvent.keyDown(textbox, { key: "Enter", code: "Enter" });

    await waitFor(() => expect(api.answerCalls).toHaveLength(1));
    expect(textbox).toHaveValue("退款规则是什么？");
  });

  it("keeps Shift+Enter as a multiline edit without submitting", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withDocuments([readyDocument("doc-a")]);
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    const textbox = screen.getByRole("textbox", { name: "输入问题" });
    await user.type(textbox, "第一行");

    await user.keyboard("{Shift>}{Enter}{/Shift}");
    await user.type(textbox, "第二行");

    expect(textbox).toHaveValue("第一行\n第二行");
    expect(api.answerCalls).toHaveLength(0);
  });

  it("does not submit an Enter key event during IME composition", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withDocuments([readyDocument("doc-a")]);
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    const textbox = screen.getByRole("textbox", { name: "输入问题" });
    await user.type(textbox, "退款规则");

    const compositionEnterWasNotCancelled = fireEvent.keyDown(textbox, {
      key: "Enter",
      code: "Enter",
      isComposing: true,
      keyCode: 229,
    });

    expect(compositionEnterWasNotCancelled).toBe(true);
    expect(api.answerCalls).toHaveLength(0);
    expect(textbox).toHaveValue("退款规则");
  });

  it("shows one honest pending block and synchronously suppresses duplicate Enter, form, and click submits", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a")])
      .deferAnswer();
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    const textbox = screen.getByRole("textbox", { name: "输入问题" });
    await user.type(textbox, "退款规则是什么？");
    const form = textbox.closest("form");
    expect(form).not.toBeNull();
    const button = screen.getByRole("button", { name: "提问" });
    let enterWasNotCancelled = true;

    act(() => {
      enterWasNotCancelled = fireEvent.keyDown(textbox, {
        key: "Enter",
        code: "Enter",
      });
      fireEvent.submit(form as HTMLFormElement);
      fireEvent.click(button);
    });

    expect(enterWasNotCancelled).toBe(false);
    await waitFor(() => expect(api.answerCalls).toHaveLength(1));
    const pending = screen.getByText("检索所选来源").closest("[aria-live]");
    expect(pending).toHaveAttribute("aria-live", "polite");
    expect(
      within(pending as HTMLElement).getByText("组织可核验回答"),
    ).toBeVisible();
    expect(screen.queryByText("😀退款需要两人审批。")).not.toBeInTheDocument();

    act(() => api.finishAnswer());
    expect(await screen.findByText("😀退款需要两人审批。")).toBeVisible();
  });

  it("aborts on selection change and ignores a non-cooperative late answer", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a"), readyDocument("doc-b")])
      .deferAnswer({ ignoreAbort: true });
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    await ask(user, "退款规则是什么？");
    expect(api.answerCalls).toHaveLength(1);

    await selectSource(user, /doc-b/u);

    expect(api.answerSignals[0]?.aborted).toBe(true);
    act(() => api.finishAnswer());
    await act(async () => Promise.resolve());
    expect(screen.queryByText("😀退款需要两人审批。")).not.toBeInTheDocument();
    expect(screen.getByText("已选择 2 个来源")).toBeVisible();
  });

  it("keeps a newer answer when two non-cooperative requests finish in reverse order", async () => {
    const user = userEvent.setup();
    const newerText = "第二次问题的回答。";
    const newerResponse = answerResponse({
      answer: newerText,
      claims: [
        {
          claimId: "claim-newer",
          text: newerText,
          answerStart: 0,
          answerEnd: Array.from(newerText).length,
          citationIds: ["citation-a"],
        },
      ],
    });
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a"), readyDocument("doc-b")])
      .deferAnswer({ ignoreAbort: true })
      .deferAnswer({ ignoreAbort: true });
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    await ask(user, "第一次问题");
    await selectSource(user, /doc-b/u);
    await ask(user, "第二次问题");
    expect(api.answerCalls).toHaveLength(2);

    act(() => api.finishAnswerAt(1, newerResponse));
    expect(await screen.findByText(newerText)).toBeVisible();
    act(() => api.finishAnswerAt(0, answerResponse()));
    await act(async () => Promise.resolve());

    expect(screen.getByText(newerText)).toBeVisible();
    expect(screen.queryByText("😀退款需要两人审批。")).not.toBeInTheDocument();
  });

  it("aborts the answer request on unmount", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a")])
      .deferAnswer();
    const rendered = renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    await ask(user, "退款规则是什么？");

    rendered.unmount();

    expect(api.answerSignals[0]?.aborted).toBe(true);
    expect(api.answerAborted).toBe(true);
  });

  it("does not clear an answer for unrelated source changes but gates it immediately when selection becomes invalid", async () => {
    const user = userEvent.setup();
    const response = answerResponse();
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a"), readyDocument("doc-b")])
      .withAnswer(response)
      .withCitation(citationPreview());
    const { queryClient } = renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    await ask(user, "退款规则是什么？");
    expect(await screen.findByText("😀退款需要两人审批。")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "引用 1" }));
    expect(await screen.findByText("原文依据")).toBeVisible();

    act(() => {
      queryClient.setQueryData<DocumentPage>(knowledgeKeys.documents(), {
        items: [
          readyDocument("doc-a"),
          document({
            documentId: "doc-b",
            filename: "doc-b.md",
            status: "failed",
            stage: "embedding",
          }),
        ],
        nextCursor: null,
      });
    });
    expect(screen.getByText("😀退款需要两人审批。")).toBeVisible();

    act(() => {
      queryClient.setQueryData<DocumentPage>(knowledgeKeys.documents(), {
        items: [
          document({
            documentId: "doc-a",
            filename: "doc-a.md",
            status: "deleting",
            stage: "ready",
          }),
        ],
        nextCursor: null,
      });
    });
    await waitFor(() => {
      expect(
        screen.queryByText("😀退款需要两人审批。"),
      ).not.toBeInTheDocument();
      expect(screen.queryByText("原文依据")).not.toBeInTheDocument();
      expect(screen.getByText("已选择 0 个来源")).toBeVisible();
    });
  });

  it("renders a safe outage instead of disguising a 503 as abstention", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a")])
      .withAnswerProblem(
        new KnowledgeClientError({
          type: "https://tap.example/problems/search-unavailable",
          title: "provider=/srv/private",
          status: 503,
          detail: "secret=sk-provider",
        }),
      );
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    await ask(user, "退款规则是什么？");

    expect(
      await screen.findByText("检索服务暂时不可用，请稍后重试。"),
    ).toBeVisible();
    expect(screen.queryByText(/证据不足/u)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/sk-provider|\/srv\/private/u),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["insufficient_evidence", "所选来源中没有足够证据回答这个问题。"],
    ["conflicting_sources", "所选来源之间存在冲突，暂时无法给出可靠回答。"],
    ["revision_mismatch", "来源版本已经变化，请重新提交问题。"],
  ] as const)("renders the %s abstention honestly", (reason, copy) => {
    const onOpenCitation = vi.fn();
    renderKnowledgeApp(
      <GroundedAnswer
        response={answerResponse({
          answer: "",
          abstained: true,
          abstentionReason: reason,
          claims: [],
          citations: [retrievalCitation(`citation-${reason}`)],
        })}
        onOpenCitation={onOpenCitation}
      />,
      { api: fakeKnowledgeClient() },
    );

    expect(screen.getByText(copy)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /引用/u }),
    ).not.toBeInTheDocument();
    expect(onOpenCitation).not.toHaveBeenCalled();
  });

  it.each([
    ["undefined", undefined],
    ["null", null],
  ])(
    "renders a controlled format error for a successful %s answer body",
    async (_name, body) => {
      const user = userEvent.setup();
      const api = fakeKnowledgeClient()
        .withDocuments([readyDocument("doc-a")])
        .withAnswer(body as unknown as RetrievalAnswerResponse);
      renderKnowledgeApp(<AthenaWorkspace />, { api });
      await selectSource(user, /doc-a/u);
      await ask(user, "退款规则是什么？");

      expect(
        await screen.findByText("回答格式无法核验，请重新提问。"),
      ).toBeVisible();
      expect(
        screen.queryByText("选择来源并提问后，回答会显示在这里。"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /引用/u }),
      ).not.toBeInTheDocument();
    },
  );
});

describe("AthenaWorkspace claim and citation integrity", () => {
  it("uses code-point offsets, original claim order, and claim-local stable citation numbers", () => {
    const onOpen = vi.fn();
    const first = retrievalCitation("citation-a");
    const second = retrievalCitation("citation-b", { chunkId: "chunk-b" });
    const response = answerResponse({
      answer: "😀退款需要两人审批。\n\n额度为五万元。",
      claims: [
        {
          claimId: "claim-a",
          text: "😀退款需要两人审批。",
          answerStart: 0,
          answerEnd: 10,
          citationIds: ["citation-b", "citation-a"],
        },
        {
          claimId: "claim-b",
          text: "额度为五万元。",
          answerStart: 12,
          answerEnd: 19,
          citationIds: ["citation-a"],
        },
      ],
      citations: [first, second],
    });
    renderKnowledgeApp(
      <GroundedAnswer response={response} onOpenCitation={onOpen} />,
      { api: fakeKnowledgeClient() },
    );

    const claims = globalThis.document.querySelectorAll(
      ".athena-grounded-claim",
    );
    expect(claims).toHaveLength(2);
    expect(
      within(claims[0] as HTMLElement).getByText("😀退款需要两人审批。"),
    ).toBeVisible();
    expect(
      within(claims[0] as HTMLElement)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["引用 2", "引用 1"]);
    expect(
      within(claims[1] as HTMLElement)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["引用 1"]);
  });

  it.each([
    [
      "text mismatch",
      (value: RetrievalAnswerResponse) => ({
        ...value,
        claims: [{ ...value.claims[0]!, text: "错误文本" }],
      }),
    ],
    [
      "out of range",
      (value: RetrievalAnswerResponse) => ({
        ...value,
        claims: [{ ...value.claims[0]!, answerEnd: 99 }],
      }),
    ],
    [
      "empty citations",
      (value: RetrievalAnswerResponse) => ({
        ...value,
        claims: [{ ...value.claims[0]!, citationIds: [] }],
      }),
    ],
    [
      "missing citation",
      (value: RetrievalAnswerResponse) => ({
        ...value,
        claims: [{ ...value.claims[0]!, citationIds: ["missing"] }],
      }),
    ],
    [
      "fractional offset",
      (value: RetrievalAnswerResponse) => ({
        ...value,
        claims: [{ ...value.claims[0]!, answerStart: 0.5 }],
      }),
    ],
    [
      "overlap",
      (value: RetrievalAnswerResponse) => ({
        ...value,
        claims: [value.claims[0]!, { ...value.claims[0]!, claimId: "claim-b" }],
      }),
    ],
    [
      "reversed original order",
      () =>
        answerResponse({
          answer: "第一段。\n\n第二段。",
          claims: [
            {
              claimId: "second",
              text: "第二段。",
              answerStart: 6,
              answerEnd: 10,
              citationIds: ["citation-a"],
            },
            {
              claimId: "first",
              text: "第一段。",
              answerStart: 0,
              answerEnd: 4,
              citationIds: ["citation-a"],
            },
          ],
        }),
    ],
  ])("fails closed for %s", (_name, mutate) => {
    const onOpen = vi.fn();
    renderKnowledgeApp(
      <GroundedAnswer
        response={mutate(answerResponse())}
        onOpenCitation={onOpen}
      />,
      { api: fakeKnowledgeClient() },
    );

    expect(screen.getByText("回答格式无法核验，请重新提问。")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /引用/u }),
    ).not.toBeInTheDocument();
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("keeps A's non-cooperative late preview from replacing B", async () => {
    const user = userEvent.setup();
    const response = answerResponse({
      claims: [
        {
          claimId: "claim-a",
          text: "😀退款需要两人审批。",
          answerStart: 0,
          answerEnd: 10,
          citationIds: ["citation-a", "citation-b"],
        },
      ],
      citations: [
        retrievalCitation("citation-a"),
        retrievalCitation("citation-b", { chunkId: "chunk-b" }),
      ],
    });
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a")])
      .withAnswer(response)
      .deferCitation("citation-a", { ignoreAbort: true })
      .deferCitation("citation-b", { ignoreAbort: true });
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    await ask(user, "退款规则是什么？");

    await user.click(await screen.findByRole("button", { name: "引用 1" }));
    await user.click(screen.getByRole("button", { name: "引用 2" }));
    expect(api.citationSignals[0]?.aborted).toBe(true);
    act(() =>
      api.finishCitation(
        "citation-b",
        citationPreview({ citationId: "citation-b", quote: "B 的原文" }),
      ),
    );
    expect(await screen.findByText("B 的原文")).toBeVisible();
    act(() =>
      api.finishCitation(
        "citation-a",
        citationPreview({ citationId: "citation-a", quote: "A 的迟到原文" }),
      ),
    );
    await act(async () => Promise.resolve());
    expect(screen.queryByText("A 的迟到原文")).not.toBeInTheDocument();
    expect(screen.getByText("B 的原文")).toBeVisible();
  });

  it("hides seeded cache, re-resolves on reopen, and restores citation-button focus on close", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a")])
      .withAnswer(answerResponse())
      .deferCitation("citation-a", { ignoreAbort: true });
    const { queryClient } = renderKnowledgeApp(<AthenaWorkspace />, { api });
    queryClient.setQueryData(
      knowledgeKeys.citation("citation-a", 3),
      citationPreview({ quote: "不应显示的缓存" }),
    );
    await selectSource(user, /doc-a/u);
    await ask(user, "退款规则是什么？");
    const citationButton = await screen.findByRole("button", {
      name: "引用 1",
    });

    await user.click(citationButton);
    expect(screen.queryByText("不应显示的缓存")).not.toBeInTheDocument();
    expect(screen.getByText("正在核验原文")).toBeVisible();
    act(() => api.finishCitation("citation-a", citationPreview()));
    expect(await screen.findByText("原文依据")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "关闭原文" }));
    await waitFor(() => expect(citationButton).toHaveFocus());

    api.deferCitation("citation-a", { ignoreAbort: true });
    await user.click(citationButton);
    expect(api.citationCalls).toEqual(["citation-a", "citation-a"]);
    expect(screen.queryByText("原文依据")).not.toBeInTheDocument();
    expect(screen.getByText("正在核验原文")).toBeVisible();
    act(() => api.finishCitation("citation-a", citationPreview()));
  });

  it("hides a prior preview when exact revalidation becomes citation-stale", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a")])
      .withAnswer(answerResponse())
      .withCitation(citationPreview());
    const { queryClient } = renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    await ask(user, "退款规则是什么？");
    await user.click(await screen.findByRole("button", { name: "引用 1" }));
    expect(await screen.findByText("原文依据")).toBeVisible();

    api.withCitationProblem(
      new KnowledgeClientError({
        type: "https://tap.example/problems/citation-stale",
        title: "provider secret",
        status: 404,
        detail: "provider secret",
      }),
    );
    await act(async () => {
      await queryClient.refetchQueries({
        queryKey: knowledgeKeys.citations(),
        type: "active",
      });
    });

    expect(
      await screen.findByText("引用已失效，来源可能已经变化，请重新提交问题。"),
    ).toBeVisible();
    const citationRegion = screen.getByRole("region", { name: "原文" });
    expect(
      within(citationRegion).queryByText("原文依据"),
    ).not.toBeInTheDocument();
    expect(within(citationRegion).queryByText("前文")).not.toBeInTheDocument();
  });

  it("fails closed when resolver provenance does not match the current answer citation", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([readyDocument("doc-a")])
      .withAnswer(answerResponse())
      .withCitation(
        citationPreview({ chunkContentHash: `sha256:${"c".repeat(64)}` }),
      );
    renderKnowledgeApp(<AthenaWorkspace />, { api });
    await selectSource(user, /doc-a/u);
    await ask(user, "退款规则是什么？");
    await user.click(await screen.findByRole("button", { name: "引用 1" }));

    expect(
      await screen.findByText("原文校验失败，请重新提交问题。"),
    ).toBeVisible();
    expect(screen.queryByText("原文依据")).not.toBeInTheDocument();
  });

  it("keeps source, question, and preview in DOM order without nesting another main", async () => {
    renderKnowledgeApp(<AthenaWorkspace />, {
      api: fakeKnowledgeClient().withDocuments([readyDocument("doc-a")]),
    });
    const source = await screen.findByRole("heading", { name: "来源" });
    const question = screen.getByRole("heading", { name: "问答" });
    const preview = screen.getByRole("heading", { name: "原文" });

    expect(
      source.compareDocumentPosition(question) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      question.compareDocumentPosition(preview) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      source.closest(".athena-workspace")?.querySelector("main"),
    ).toBeNull();
  });

  it("keeps keyboard focus rings and the search clear target locally visible", () => {
    expect(workspaceStyles).toMatch(
      /\.athena-workspace \.ant-input:focus-visible,[\s\S]*outline:\s*3px solid[^;]*!important;/u,
    );
    expect(workspaceStyles).toMatch(
      /\.athena-source-search \.ant-input-clear-icon\s*\{[\s\S]*?min-width:\s*44px;[\s\S]*?min-height:\s*44px;/u,
    );
  });
});
