import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { expect, it } from "vitest";

import { UploadChunkSettings } from "./UploadChunkSettings";
import { DEFAULT_CHUNK_SETTINGS, type ChunkSettings } from "./ChunkManager";
import { PROTOTYPE_COPY } from "./copy";

const copy = PROTOTYPE_COPY.en;

function Harness() {
  const [value, setValue] = useState<ChunkSettings>(DEFAULT_CHUNK_SETTINGS);
  return <UploadChunkSettings copy={copy} value={value} onChange={setValue} />;
}

it("previews the first chunks for the chosen settings", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "Preview chunks" }));
  expect(screen.getAllByRole("listitem")).toHaveLength(3);

  fireEvent.click(screen.getByRole("radio", { name: "Parent-child" }));
  fireEvent.click(screen.getByRole("button", { name: "Preview chunks" }));
  const items = screen.getAllByRole("listitem");
  expect(items).toHaveLength(3);
  items.forEach((item) => {
    expect(within(item).getByText(/child chunks/)).toBeVisible();
  });
});

it("exposes maximum length, overlap and child maximum length controls", () => {
  render(<Harness />);
  expect(
    screen.getByRole("spinbutton", { name: "Maximum length" }),
  ).toBeVisible();
  expect(screen.getByRole("spinbutton", { name: "Overlap" })).toBeVisible();
  expect(
    screen.queryByRole("spinbutton", { name: "Child maximum length" }),
  ).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("radio", { name: "Parent-child" }));
  expect(
    screen.getByRole("spinbutton", { name: "Child maximum length" }),
  ).toBeVisible();
});

it("reports max length changes through onChange", () => {
  render(<Harness />);
  fireEvent.change(screen.getByRole("spinbutton", { name: "Maximum length" }), {
    target: { value: "300" },
  });
  expect(
    screen.getByRole("spinbutton", { name: "Maximum length" }),
  ).toHaveValue(300);
});
