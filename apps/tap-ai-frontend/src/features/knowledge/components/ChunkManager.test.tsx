import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { renderApp } from "../../../shared/testing/renderApp";
import { ChunkManager } from "./ChunkManager";

const chunk = {
  chunkId: "c1",
  content: "Original content",
  enabled: true,
  edited: false,
  position: 1,
  charCount: 16,
  tokens: 4,
  keywords: [],
  summary: null,
  children: [],
  indexStatus: "ready",
  indexError: null,
  version: 1,
};
afterEach(() => vi.unstubAllGlobals());
it("keeps an edit draft when the server rejects its stale version", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PATCH"
        ? new Response("{}", { status: 409 })
        : Response.json({ items: [chunk], total: 1, page: 1, pageSize: 20 }),
    ),
  );
  renderApp(<ChunkManager projectId="p" documentId="d" />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑切片 1" }));
  fireEvent.change(screen.getByLabelText("切片内容"), {
    target: { value: "My new draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存并索引" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("版本已变化");
  expect(screen.getByLabelText("切片内容")).toHaveValue("My new draft");
});
it("sends search and pagination to the server and shows the edited state", async () => {
  const fetcher = vi.fn<(url: string) => Promise<Response>>(async () =>
    Response.json({
      items: [{ ...chunk, edited: true }],
      total: 25,
      page: 1,
      pageSize: 20,
    }),
  );
  vi.stubGlobal("fetch", fetcher);
  renderApp(<ChunkManager projectId="p" documentId="d" />);
  expect(await screen.findByText("已编辑")).toBeVisible();
  fireEvent.change(screen.getByLabelText("搜索切片内容"), {
    target: { value: "needle" },
  });
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some((call) => String(call[0]).includes("q=needle")),
    ).toBe(true),
  );
});
it("allows reading a full document parent but disables editing its content", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ items: [chunk], total: 1, page: 1, pageSize: 20 }),
    ),
  );
  renderApp(
    <ChunkManager projectId="p" documentId="d" fullDocument parentChild />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "编辑切片 1" }));
  expect(screen.getByLabelText("切片内容")).toHaveAttribute("readonly");
  expect(
    screen.queryByRole("button", { name: "保存并索引" }),
  ).not.toBeInTheDocument();
});
it("does not offer child regeneration while editing a child", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        items: [
          { ...chunk, children: [{ ...chunk, chunkId: "child", position: 2 }] },
        ],
        total: 1,
        page: 1,
        pageSize: 20,
      }),
    ),
  );
  renderApp(<ChunkManager projectId="p" documentId="d" parentChild />);
  fireEvent.click(await screen.findByText("1 个子块"));
  fireEvent.click(screen.getByRole("button", { name: "编辑切片 2" }));
  expect(
    screen.queryByRole("checkbox", { name: /重新生成子块/ }),
  ).not.toBeInTheDocument();
});
it("removes selected chunks only after confirmed server deletion and reload", async () => {
  let items = [chunk];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        items = [];
        return Response.json({ succeeded: ["c1"], failed: [] });
      }
      return Response.json({
        items,
        total: items.length,
        page: 1,
        pageSize: 20,
      });
    }),
  );
  renderApp(<ChunkManager projectId="p" documentId="d" />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "选择切片 1" }));
  fireEvent.click(screen.getByRole("button", { name: "批量删除" }));
  expect(screen.getByText("Original content")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
  expect(await screen.findByText("没有匹配的切片")).toBeVisible();
  expect(screen.queryByText("Original content")).not.toBeInTheDocument();
});
it("preserves child blocks when the parent regeneration option is cleared", async () => {
  let content = "Original content";
  let childContent = "Child original";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)) as {
          content: string;
          regenerateChildren: boolean;
        };
        content = body.content;
        if (body.regenerateChildren) childContent = "Regenerated child";
        return Response.json({ ...chunk, content });
      }
      return Response.json({
        items: [
          {
            ...chunk,
            content,
            children: [
              {
                ...chunk,
                chunkId: "child",
                position: 2,
                content: childContent,
              },
            ],
          },
        ],
        total: 1,
        page: 1,
        pageSize: 20,
      });
    }),
  );
  renderApp(<ChunkManager projectId="p" documentId="d" parentChild />);
  fireEvent.click(await screen.findByRole("button", { name: "编辑切片 1" }));
  fireEvent.change(screen.getByLabelText("切片内容"), {
    target: { value: "Parent edited" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: /重新生成子块/ }));
  fireEvent.click(screen.getByRole("button", { name: "保存并索引" }));
  expect(
    await screen.findByText("Parent edited", { selector: "p" }),
  ).toBeVisible();
  fireEvent.click(screen.getByText("1 个子块"));
  expect(screen.getByText("Child original")).toBeVisible();
});
it("imports a CSV content column through the real import adapter", async () => {
  let imported: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        imported = (JSON.parse(String(init.body)) as { contents: string[] })
          .contents;
        return Response.json({ succeeded: ["new"], failed: [] });
      }
      return Response.json({ items: [], total: 0, page: 1, pageSize: 20 });
    }),
  );
  renderApp(<ChunkManager projectId="p" documentId="d" />);
  fireEvent.click(screen.getByRole("button", { name: "批量导入" }));
  fireEvent.change(screen.getByLabelText("选择 CSV 文件"), {
    target: {
      files: [
        new File(
          ['\ufeffcontent\r\n"Hello, reader"\r\n"Line one\nLine two"'],
          "chunks.csv",
          { type: "text/csv" },
        ),
      ],
    },
  });
  await screen.findByText("已读取 2 个切片");
  fireEvent.click(screen.getByRole("button", { name: "保存并索引" }));
  await waitFor(() =>
    expect(imported).toEqual(["Hello, reader", "Line one\nLine two"]),
  );
});
it("keeps a CSV parse error visible and does not submit it", async () => {
  const fetcher = vi.fn(async () =>
    Response.json({ items: [], total: 0, page: 1, pageSize: 20 }),
  );
  vi.stubGlobal("fetch", fetcher);
  renderApp(<ChunkManager projectId="p" documentId="d" />);
  fireEvent.click(screen.getByRole("button", { name: "批量导入" }));
  fireEvent.change(screen.getByLabelText("选择 CSV 文件"), {
    target: {
      files: [
        new File(["wrong\nno content"], "invalid.csv", { type: "text/csv" }),
      ],
    },
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("content");
  expect(screen.getByRole("button", { name: "保存并索引" })).toBeDisabled();
});
