import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  document,
  documentDetail,
  fakeKnowledgeClient,
  markdownFile,
} from "../testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../testing/renderKnowledgeApp";
import { KnowledgeClientError } from "../api/client";
import { KnowledgeLibrary } from "./KnowledgeLibrary";

describe("KnowledgeLibrary", () => {
  it("shows one clear add-source action in an empty library", async () => {
    renderKnowledgeApp(<KnowledgeLibrary />, { api: fakeKnowledgeClient() });

    expect(
      await screen.findByRole("heading", { name: "知识库" }),
    ).toBeVisible();
    expect(await screen.findByText("还没有来源")).toBeVisible();
    expect(screen.getAllByRole("button", { name: "添加来源" })).toHaveLength(1);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("opens upload from the keyboard and restores focus after cancel", async () => {
    const user = userEvent.setup();
    renderKnowledgeApp(<KnowledgeLibrary />, { api: fakeKnowledgeClient() });
    const addSource = await screen.findByRole("button", {
      name: "添加来源",
    });

    addSource.focus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog", { name: "添加来源" });
    const input = within(dialog).getByLabelText("选择文档");
    const dropZone = within(dialog).getByRole("button", {
      name: "拖放或选择文档",
    });
    expect(input).toHaveAttribute("tabindex", "-1");
    dropZone.focus();
    await user.tab();
    expect(
      within(dialog).getByRole("button", { name: /取\s*消/u }),
    ).toHaveFocus();

    await user.click(within(dialog).getByRole("button", { name: /取\s*消/u }));
    await waitFor(() => expect(addSource).toHaveFocus());
  });

  it("rejects invalid or oversized files before I/O", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const api = fakeKnowledgeClient();
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    await user.click(await screen.findByRole("button", { name: "添加来源" }));
    const input = screen.getByLabelText("选择文档");

    expect(input).toHaveAttribute("accept", ".pdf,.docx,.md,.markdown,.txt");
    await user.upload(
      input,
      new File(["csv"], "records.csv", { type: "text/csv" }),
    );
    await waitFor(() =>
      expect(
        within(screen.getByRole("dialog", { name: "添加来源" })).getByText(
          "支持 PDF、DOCX、Markdown 和 TXT 文件。",
        ),
      ).toBeVisible(),
    );
    expect(api.uploadCalls).toBe(0);

    await user.upload(
      input,
      markdownFile("oversized.md", 25 * 1024 * 1024 + 1),
    );
    await waitFor(() =>
      expect(
        within(screen.getByRole("dialog", { name: "添加来源" })).getByText(
          "文件超过 25 MiB，请选择更小的文档。",
        ),
      ).toBeVisible(),
    );
    expect(api.uploadCalls).toBe(0);
  });

  it.each([
    ["guide.pdf", "application/pdf"],
    [
      "guide.docx",
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
    ["guide.markdown", "text/markdown"],
    ["guide.txt", "text/plain"],
  ])("accepts and uploads supported file %s", async (name, mediaType) => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient();
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    await user.click(await screen.findByRole("button", { name: "添加来源" }));

    await user.upload(
      screen.getByLabelText("选择文档"),
      new File(["supported"], name, { type: mediaType }),
    );
    await user.click(screen.getByRole("button", { name: "开始添加" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(api.uploadCalls).toBe(1);
  });

  it("shows upload progress, closes after 202, and keeps the receipt visible", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient();
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    await user.click(await screen.findByRole("button", { name: "添加来源" }));
    await user.upload(screen.getByLabelText("选择文档"), markdownFile());
    await user.click(screen.getByRole("button", { name: "开始添加" }));

    expect(await screen.findByText("上传 52%")).toBeVisible();
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByRole("row", { name: /policy\.md/ }),
    ).toBeVisible();
    expect(api.uploadCalls).toBe(1);
  });

  it("closes immediately after the 202 receipt even when list reconciliation is still pending", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient();
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    expect(await screen.findByText("还没有来源")).toBeVisible();
    api.deferList();
    await user.click(screen.getByRole("button", { name: "添加来源" }));
    await user.upload(screen.getByLabelText("选择文档"), markdownFile());
    await user.click(screen.getByRole("button", { name: "开始添加" }));

    expect(
      await screen.findByRole("row", { name: /policy\.md/ }),
    ).toBeVisible();
    try {
      await waitFor(
        () => expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
        { timeout: 500 },
      );
    } finally {
      api.finishList();
    }
  });

  it("prevents replacing the selected file while its upload is pending", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().deferUpload();
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    await user.click(await screen.findByRole("button", { name: "添加来源" }));
    const input = screen.getByLabelText("选择文档");
    await user.upload(input, markdownFile("original.md"));
    await user.click(screen.getByRole("button", { name: "开始添加" }));

    const dropZone = screen.getByRole("button", {
      name: "拖放或选择文档",
    });
    expect(await screen.findByText("上传 52%")).toBeVisible();
    expect(input).toBeDisabled();
    expect(dropZone).toHaveAttribute("aria-disabled", "true");
    expect(dropZone).toHaveAttribute("tabindex", "-1");
    fireEvent.change(input, {
      target: {
        files: [new File(["replacement"], "replacement.txt")],
      },
    });
    expect(screen.getByText("original.md")).toBeVisible();
    expect(screen.queryByText("replacement.txt")).not.toBeInTheDocument();

    api.finishUpload();
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("labels a duplicate receipt without creating a second visual source", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withDuplicateUpload();
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    await user.click(await screen.findByRole("button", { name: "添加来源" }));
    await user.upload(screen.getByLabelText("选择文档"), markdownFile());
    await user.click(screen.getByRole("button", { name: "开始添加" }));

    expect(
      await screen.findByText("这个内容已经在知识库中，已显示现有来源。"),
    ).toBeVisible();
    expect(screen.getAllByText("handbook.md")).toHaveLength(1);
    expect(screen.queryByText("policy.md")).not.toBeInTheDocument();
  });

  it("aborts an in-flight upload when the library unmounts", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient();
    const { unmount } = renderKnowledgeApp(<KnowledgeLibrary />, { api });
    await user.click(await screen.findByRole("button", { name: "添加来源" }));
    await user.upload(screen.getByLabelText("选择文档"), markdownFile());
    await user.click(screen.getByRole("button", { name: "开始添加" }));

    unmount();

    expect(api.uploadAborted).toBe(true);
  });

  it("polls processing to failed and stops when every row is terminal", async () => {
    vi.useFakeTimers();
    const api = fakeKnowledgeClient()
      .listOnce([document({ status: "processing", stage: "embedding" })])
      .listOnce([
        document({
          status: "failed",
          stage: "embedding",
          errorCode: "embedding-unavailable",
          errorSummary: "向量服务暂时不可用。",
        }),
      ]);
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    await act(async () => vi.advanceTimersByTimeAsync(0));

    expect(screen.getByText("正在生成向量")).toBeVisible();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
      await vi.runOnlyPendingTimersAsync();
    });
    expect(screen.getByRole("button", { name: "重试" })).toBeEnabled();
    expect(api.listCalls).toBe(2);

    await act(async () => vi.advanceTimersByTimeAsync(4_000));
    expect(api.listCalls).toBe(2);
    vi.useRealTimers();
  });

  it("shows ready, deleting, processing, and failed operational copy", async () => {
    const api = fakeKnowledgeClient().withDocuments([
      document({
        documentId: "ready",
        filename: "ready.md",
        status: "ready",
        stage: "ready",
      }),
      document({
        documentId: "deleting",
        filename: "old.md",
        status: "deleting",
        stage: "ready",
      }),
      document({
        documentId: "processing",
        filename: "parse.pdf",
        status: "processing",
        stage: "parsing",
      }),
      document({
        documentId: "failed",
        filename: "broken.docx",
        status: "failed",
        stage: "embedding",
        errorSummary: "内容无法解析。",
      }),
    ]);
    renderKnowledgeApp(<KnowledgeLibrary />, { api });

    expect(await screen.findByText("已就绪")).toBeVisible();
    expect(screen.getByText("正在删除")).toBeVisible();
    expect(screen.getByText("正在解析内容")).toBeVisible();
    expect(screen.getByText("处理失败")).toBeVisible();
    expect(screen.getByText("失败阶段：生成向量")).toBeVisible();
    expect(screen.getByText("内容无法解析。")).toBeVisible();
  });

  it("counts all 50 visible rows without treating deleting as ready or queued as idle", async () => {
    const documents = Array.from({ length: 50 }, (_, index) => {
      if (index < 25) {
        return document({
          documentId: `ready-${index}`,
          filename: `ready-${index}.md`,
          status: "ready",
          stage: "ready",
        });
      }
      if (index < 48) {
        return document({
          documentId: `processing-${index}`,
          filename: `processing-${index}.md`,
          status: "processing",
          stage: "chunking",
        });
      }
      if (index === 48) {
        return document({
          documentId: "queued-48",
          filename: "queued-48.md",
          status: "queued",
        });
      }
      return document({
        documentId: "deleting-49",
        filename: "deleting-49.md",
        status: "deleting",
        stage: "ready",
      });
    });
    renderKnowledgeApp(<KnowledgeLibrary />, {
      api: fakeKnowledgeClient().withDocuments(documents),
    });

    expect(await screen.findByText("已就绪 25")).toBeVisible();
    expect(screen.getByText("处理中 24")).toBeVisible();
    expect(screen.getByText("失败 0")).toBeVisible();
    expect(screen.getByText("deleting-49.md")).toBeVisible();
  });

  it("shows a safe load error instead of disguising it as an empty library", async () => {
    const problem = {
      type: "https://tap.local/problems/knowledge-unavailable",
      title: "Provider /srv/private error",
      status: 503,
      detail: "secret=sk-live-provider in /srv/private",
    };
    renderKnowledgeApp(<KnowledgeLibrary />, {
      api: fakeKnowledgeClient().withListProblem(
        new KnowledgeClientError(problem as never),
      ),
    });

    expect(
      await screen.findByText("暂时无法加载知识库，请稍后重试。"),
    ).toBeVisible();
    expect(screen.queryByText("还没有来源")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/sk-live-provider|\/srv\/private/),
    ).not.toBeInTheDocument();
  });

  it("opens detail from a focused row, shows immutable facts and all six stages, then restores focus", async () => {
    const user = userEvent.setup();
    const summary = document({
      status: "ready",
      stage: "ready",
      chunkCount: 12,
    });
    const api = fakeKnowledgeClient()
      .withDocuments([summary])
      .withDetail(
        documentDetail({
          ...summary,
          normalizedPreview: "Tapper 只依据已选择的来源回答问题。",
        }),
      );
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    const row = await screen.findByRole("row", { name: /handbook\.md/ });

    row.focus();
    await user.keyboard("{Enter}");

    const detail = await screen.findByRole("dialog", {
      name: "handbook.md 详情",
    });
    expect(within(detail).getByText("rev_01JABCDEF")).toBeVisible();
    expect(within(detail).getByText(/749d926c8783/)).toBeVisible();
    expect(
      within(detail).getByText("Tapper 只依据已选择的来源回答问题。"),
    ).toBeVisible();
    for (const stage of [
      "保存源文件",
      "解析内容",
      "整理片段",
      "生成向量",
      "发布索引",
      "可用于问答",
    ]) {
      expect(within(detail).getByText(stage)).toBeVisible();
    }

    await user.click(within(detail).getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(row).toHaveFocus());
  });

  it("states honestly when a normalized preview is not available yet", async () => {
    const user = userEvent.setup();
    const summary = document({ status: "ready", stage: "ready" });
    const api = fakeKnowledgeClient()
      .withDocuments([summary])
      .withDetail(documentDetail({ ...summary, normalizedPreview: null }));
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    const row = await screen.findByRole("row", { name: /handbook\.md/ });

    await user.click(row);

    expect(
      await screen.findByText("暂时没有可显示的规范化预览。"),
    ).toBeVisible();
  });

  it("opens a focused source row with Space", async () => {
    const user = userEvent.setup();
    const summary = document({ status: "ready", stage: "ready" });
    const api = fakeKnowledgeClient()
      .withDocuments([summary])
      .withDetail(documentDetail(summary));
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    const row = await screen.findByRole("row", { name: /handbook\.md/u });

    row.focus();
    await user.keyboard(" ");

    expect(
      await screen.findByRole("dialog", { name: "handbook.md 详情" }),
    ).toBeVisible();
  });

  it("renders normalized preview as inert plain text", async () => {
    const user = userEvent.setup();
    const summary = document({ status: "ready", stage: "ready" });
    const preview = "<script>window.__unsafe = true</script>";
    const api = fakeKnowledgeClient()
      .withDocuments([summary])
      .withDetail(documentDetail({ ...summary, normalizedPreview: preview }));
    renderKnowledgeApp(<KnowledgeLibrary />, { api });

    await user.click(await screen.findByRole("row", { name: /handbook\.md/ }));

    const detail = await screen.findByRole("dialog", {
      name: "handbook.md 详情",
    });
    expect(within(detail).getByText(preview)).toBeVisible();
    expect(detail.querySelector("script")).toBeNull();
  });

  it("retries a failed document without resetting the visible row first", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([
        document({
          status: "failed",
          stage: "embedding",
          errorCode: "embedding-unavailable",
        }),
      ])
      .deferRetry();
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    const retry = await screen.findByRole("button", { name: "重试" });

    await user.click(retry);

    expect(api.retryCalls).toEqual(["doc-1"]);
    expect(screen.getByText("处理失败")).toBeVisible();
    expect(retry).toBeDisabled();

    act(() => api.finishRetry());
    expect(await screen.findByText("等待处理")).toBeVisible();
    expect(screen.getByRole("row", { name: /handbook\.md/ })).toBeVisible();
  });

  it("confirms delete with the filename, disables the row immediately, removes it after 204, and restores focus", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient()
      .withDocuments([document({ status: "ready", stage: "ready" })])
      .deferDelete();
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    const remove = await screen.findByRole("button", {
      name: "删除 handbook.md",
    });
    remove.focus();
    await user.keyboard("{Enter}");
    const confirmation = screen.getByRole("dialog", { name: "删除来源" });
    expect(
      screen.queryByRole("dialog", { name: "handbook.md 详情" }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(within(confirmation).getByText(/handbook\.md/)).toBeVisible(),
    );

    await user.click(
      within(confirmation).getByRole("button", { name: "确认删除" }),
    );
    expect(await screen.findByText("正在删除")).toBeVisible();
    expect(screen.getByRole("row", { name: /handbook\.md/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );

    act(() => api.finishDelete());
    await waitFor(() =>
      expect(screen.queryByText("handbook.md")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "添加来源" })).toHaveFocus();
  });

  it("restores focus to the delete trigger when confirmation is cancelled", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withDocuments([
      document({ status: "ready", stage: "ready" }),
    ]);
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    const remove = await screen.findByRole("button", {
      name: "删除 handbook.md",
    });

    await user.click(remove);
    const confirmation = screen.getByRole("dialog", { name: "删除来源" });
    await user.click(
      within(confirmation).getByRole("button", { name: /取\s*消/u }),
    );

    await waitFor(() => expect(remove).toHaveFocus());
    expect(api.deleteCalls).toEqual([]);
  });

  it("renders actionable safe Problem Details copy and redacts provider detail", async () => {
    const user = userEvent.setup();
    const problem = {
      type: "https://tap.local/problems/document-too-large",
      title: "Provider /srv/private error",
      status: 413,
      detail: "secret=sk-live-provider in /srv/private",
    };
    const api = fakeKnowledgeClient().withUploadProblem(
      new KnowledgeClientError(problem as never),
    );
    renderKnowledgeApp(<KnowledgeLibrary />, { api });
    await user.click(await screen.findByRole("button", { name: "添加来源" }));
    await user.upload(screen.getByLabelText("选择文档"), markdownFile());
    await user.click(screen.getByRole("button", { name: "开始添加" }));

    expect(
      await screen.findByText("文件超过服务端允许的大小，请选择更小的文档。"),
    ).toBeVisible();
    expect(
      screen.queryByText(/sk-live-provider|\/srv\/private/),
    ).not.toBeInTheDocument();
  });
});
