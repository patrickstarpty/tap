import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  document,
  fakeKnowledgeClient,
} from "../features/knowledge/testing/fakeKnowledgeClient";
import { renderKnowledgeApp } from "../features/knowledge/testing/renderKnowledgeApp";
import { AthenaPage } from "./AthenaPage";

describe("AthenaPage", () => {
  it("keeps the terminal document cache across primary navigation changes", async () => {
    const user = userEvent.setup();
    const api = fakeKnowledgeClient().withDocuments([
      document({ status: "ready", stage: "ready" }),
    ]);
    renderKnowledgeApp(<AthenaPage />, { api });
    await user.click(screen.getByRole("tab", { name: "知识库" }));
    expect(await screen.findByText("handbook.md")).toBeVisible();
    expect(api.listCalls).toBe(1);

    await user.click(screen.getByRole("tab", { name: "问答" }));
    expect(screen.getByText("从已就绪的来源开始提问")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "知识库" }));

    expect(screen.getByText("handbook.md")).toBeVisible();
  });

  it("shows cached processing state immediately after tab return and resumes polling", async () => {
    const api = fakeKnowledgeClient()
      .listOnce([document({ status: "processing", stage: "embedding" })])
      .listOnce([
        document({
          status: "failed",
          stage: "embedding",
          errorCode: "embedding-unavailable",
        }),
      ]);
    const { queryClient } = renderKnowledgeApp(
      <AthenaPage knowledgePollIntervalMs={25} />,
      { api },
    );
    const tabs = screen.getByRole("tablist");

    fireEvent.click(screen.getByRole("tab", { name: "知识库" }));
    expect(await screen.findByText("正在生成向量")).toBeVisible();
    fireEvent.click(screen.getByRole("tab", { name: "问答" }));
    fireEvent.click(screen.getByRole("tab", { name: "知识库" }));
    expect(screen.getByText("正在生成向量")).toBeVisible();

    await waitFor(
      () =>
        expect(
          queryClient.getQueryData<{ items: Array<{ status: string }> }>([
            "knowledge",
            "documents",
          ])?.items[0]?.status,
        ).toBe("failed"),
      { timeout: 1_000 },
    );
    await waitFor(() =>
      expect(screen.queryByText("处理失败")).toBeInTheDocument(),
    );
    expect(api.listCalls).toBe(2);
    const retryLabel = screen.getByText("重试");
    expect(retryLabel).toBeVisible();
    expect(retryLabel.closest("button")).toBeEnabled();
    expect(tabs).toBeInTheDocument();
  });
});
