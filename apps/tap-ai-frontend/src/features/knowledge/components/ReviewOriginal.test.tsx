import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ReviewOriginal } from "./ReviewOriginal";

it("loads original only on demand and revokes the source URL on item change", async () => {
  const load = vi
    .fn()
    .mockResolvedValue(new Blob(["%PDF"], { type: "application/pdf" }));
  const create = vi.fn().mockReturnValue("blob:source");
  const revoke = vi.fn();
  vi.stubGlobal(
    "URL",
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }),
  );
  const { rerender } = render(
    <ReviewOriginal reviewId="r1" itemId="i1" locator="page:12" load={load} />,
  );
  expect(load).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查看原文件" }));
  await waitFor(() =>
    expect(screen.getByTitle("原 PDF · 第 12 页")).toHaveAttribute(
      "src",
      "blob:source#page=12",
    ),
  );
  rerender(
    <ReviewOriginal reviewId="r1" itemId="i2" locator="page:13" load={load} />,
  );
  await waitFor(() => expect(revoke).toHaveBeenCalledWith("blob:source"));
  expect(screen.queryByTitle("原 PDF · 第 12 页")).not.toBeInTheDocument();
});

it("offers download instead of embedding Office files", async () => {
  const load = vi.fn().mockResolvedValue(
    new Blob(["xlsx"], {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
  );
  render(
    <ReviewOriginal
      reviewId="r1"
      itemId="i1"
      locator="sheet:Rates/A1:D8"
      extractedText={"保障项目\t费率\t单位\n重疾\t0.35\t%"}
      load={load}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "查看原文件" }));
  expect(
    await screen.findByRole("link", { name: "下载原文件" }),
  ).toHaveAttribute("download", "source.xlsx");
  expect(screen.getByRole("table", { name: "工作表提取预览" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "费率" })).toBeVisible();
  expect(screen.getByRole("cell", { name: "0.35" })).toBeVisible();
  expect(document.querySelector("iframe")).toBeNull();
});

it("does not invent a page for a document-level PDF locator", async () => {
  const load = vi
    .fn()
    .mockResolvedValue(new Blob(["%PDF"], { type: "application/pdf" }));
  render(
    <ReviewOriginal
      reviewId="r1"
      itemId="i1"
      locator="document:parser"
      load={load}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "查看原文件" }));
  expect(await screen.findByTitle("原 PDF")).toHaveAttribute(
    "src",
    "blob:source",
  );
  expect(screen.queryByText(/第 1 页/u)).not.toBeInTheDocument();
});
