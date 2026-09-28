import { fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { renderApp } from "../../../shared/testing/renderApp";
import { UploadChunkPreview } from "./UploadChunkPreview";
import { DEFAULT_CHUNK_SETTINGS } from "../api/chunks";
afterEach(() => vi.unstubAllGlobals());
it("previews selected file using server chunking and displays actual totals", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        items: [{ content: "Preview from parser", children: [] }],
        total: 12,
      }),
    ),
  );
  renderApp(
    <UploadChunkPreview
      projectId="p"
      file={new File(["file"], "source.txt")}
      value={DEFAULT_CHUNK_SETTINGS}
      onChange={() => undefined}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "预览切片" }));
  expect(await screen.findByText("Preview from parser")).toBeVisible();
  expect(screen.getByText("预览 1 / 12 个切片")).toBeVisible();
});
