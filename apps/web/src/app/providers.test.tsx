import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeKnowledgeClient } from "../features/knowledge/testing/fakeKnowledgeClient";
import type { RuntimeMode } from "../features/runtime/api/client";
import { TapperPage } from "../pages/TapperPage";
import { createTestQueryClient } from "../shared/testing/renderApp";
import { AppProviders } from "./providers";

const mode: RuntimeMode = {
  mode: "validation",
  identityMode: "validation",
  projectId: "project-server",
  actorId: "actor-server",
};

describe("runtime composition", () => {
  it("keeps navigation available and starts Knowledge only after trusted runtime resolves", async () => {
    let resolve!: (mode: RuntimeMode) => void;
    const pending = new Promise<RuntimeMode>((done) => {
      resolve = done;
    });
    const api = fakeKnowledgeClient("project-server");
    const list = vi.spyOn(api, "listDocuments");
    const user = userEvent.setup();
    render(
      <AppProviders
        queryClient={createTestQueryClient()}
        runtimeClient={{ getMode: () => pending }}
        knowledgeClient={api}
      >
        <TapperPage />
      </AppProviders>,
    );
    expect(
      screen.getByRole("status", { name: "Validation Mode" }),
    ).toHaveTextContent("正在连接运行环境");
    expect(list).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(screen.getByRole("heading", { name: "Library" })).toBeVisible();
    await act(async () => {
      resolve(mode);
    });
    expect(
      await screen.findByText(
        "操作统一记录到固定 Validation Actor，不代表个人身份",
      ),
    ).toBeVisible();
    expect(list).toHaveBeenCalledOnce();
  });

  it("keeps an honest failure state without inventing Project or Actor authority", async () => {
    const api = fakeKnowledgeClient("project-server");
    const list = vi.spyOn(api, "listDocuments");
    render(
      <AppProviders
        queryClient={createTestQueryClient()}
        runtimeClient={{ getMode: () => Promise.reject(new Error("offline")) }}
        knowledgeClient={api}
      >
        <TapperPage />
      </AppProviders>,
    );
    expect(
      await screen.findByText("运行环境连接失败 · 服务器操作暂不可用"),
    ).toBeVisible();
    expect(
      screen.queryByText("操作统一记录到固定 Validation Actor，不代表个人身份"),
    ).not.toBeInTheDocument();
    expect(list).not.toHaveBeenCalled();
    expect(screen.getByRole("navigation", { name: "Product" })).toBeVisible();
  });
});
