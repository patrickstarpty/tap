import { defaultScheduler, notifyManager } from "@tanstack/react-query";
import { act, fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { useDocumentListQuery } from "../features/knowledge/api/queries";

import {
  document,
  fakeKnowledgeClient,
} from "../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../features/knowledge/testing/renderKnowledgeApp";
import { AthenaPage } from "./AthenaPage";

function DocumentPollingProbe({ pollIntervalMs }: { pollIntervalMs: number }) {
  const documentsQuery = useDocumentListQuery({ pollIntervalMs });
  return (
    <output aria-label="共享文档状态">
      {documentsQuery.data?.items[0]?.status ?? "pending"}
    </output>
  );
}

describe("AthenaPage", () => {
  it("offers the Intelligence Lab as a third primary workspace", async () => {
    const user = userEvent.setup();
    renderKnowledgeApp(<AthenaPage />, { api: fakeKnowledgeClient() });

    await user.click(screen.getByRole("tab", { name: "Intelligence Lab" }));

    expect(
      within(
        screen.getByRole("tabpanel", { name: "Intelligence Lab" }),
      ).getByRole("heading", { name: "把模糊目标变成可审核的自动化蓝图" }),
    ).toBeVisible();
  });

  it("keeps the terminal document cache across primary navigation changes", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withDocuments([
      document({ status: "ready", stage: "ready" }),
    ]);
    renderKnowledgeApp(<AthenaPage />, { api });
    await user.click(screen.getByRole("tab", { name: "知识库" }));
    const libraryPanel = screen.getByRole("tabpanel", { name: "知识库" });
    expect(await within(libraryPanel).findByText("handbook.md")).toBeVisible();
    expect(api.listCalls).toBe(1);

    await user.click(screen.getByRole("tab", { name: "问答" }));
    expect(
      within(screen.getByRole("tabpanel", { name: "问答" })).getByRole(
        "heading",
        { name: "问答" },
      ),
    ).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "知识库" }));

    expect(within(libraryPanel).getByText("handbook.md")).toBeVisible();
  });

  it("shows cached processing state immediately after tab return", async () => {
    const api = fakeKnowledgeClient().withDocuments([
      document({ status: "processing", stage: "embedding" }),
    ]);
    renderKnowledgeApp(<AthenaPage knowledgePollIntervalMs={60_000} />, {
      api,
    });
    expect(
      await within(screen.getByRole("tabpanel", { name: "问答" })).findByText(
        "正在生成向量",
      ),
    ).toBeVisible();
    expect(api.listCalls).toBe(1);

    fireEvent.click(screen.getByRole("tab", { name: "知识库" }));
    const libraryPanel = screen.getByRole("tabpanel", { name: "知识库" });
    expect(within(libraryPanel).getByText("正在生成向量")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "问答" }));
    fireEvent.click(screen.getByRole("tab", { name: "知识库" }));
    expect(within(libraryPanel).getByText("正在生成向量")).toBeVisible();
    expect(api.listCalls).toBe(1);
  });

  it("polls the shared processing snapshot to terminal with controlled time", async () => {
    vi.useFakeTimers();
    notifyManager.setScheduler((callback) => callback());
    const api = fakeKnowledgeClient()
      .listOnce([document({ status: "processing", stage: "embedding" })])
      .listOnce([
        document({
          status: "failed",
          stage: "embedding",
          errorCode: "embedding-unavailable",
        }),
      ]);
    const rendered = renderKnowledgeApp(
      <DocumentPollingProbe pollIntervalMs={25} />,
      { api },
    );
    try {
      await act(async () => vi.advanceTimersByTimeAsync(0));
      expect(screen.getByLabelText("共享文档状态")).toHaveTextContent(
        "processing",
      );
      expect(api.listCalls).toBe(1);

      await act(async () => vi.advanceTimersByTimeAsync(25));
      expect(screen.getByLabelText("共享文档状态")).toHaveTextContent("failed");
      expect(api.listCalls).toBe(2);

      await act(async () => vi.advanceTimersByTimeAsync(100));
      expect(api.listCalls).toBe(2);
    } finally {
      rendered.unmount();
      notifyManager.setScheduler(defaultScheduler);
      vi.useRealTimers();
    }
  });
});
