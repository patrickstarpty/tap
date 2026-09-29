import {
  act,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DocumentReview, useDocumentReview } from "./DocumentReview";
import { ChunkManager } from "./ChunkManager";
function Harness() {
  const review = useDocumentReview("en");
  return (
    <>
      <button onClick={() => review.inspect("underwriting-v12")}>Open</button>
      <DocumentReview review={review} onUse={() => {}} />
    </>
  );
}
beforeEach(() => localStorage.clear());
it("saves edited chunks directly, persists changes and prevents disabled editing", async () => {
  const view = render(<Harness />);
  fireEvent.click(screen.getByText("Open"));
  fireEvent.click(screen.getAllByRole("button", { name: "Edit chunk" })[0]!);
  fireEvent.change(screen.getByRole("textbox", { name: "Chunk content" }), {
    target: { value: "Updated underwriting rule" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save chunk" }));
  await waitFor(() =>
    expect(screen.getByText("Updated underwriting rule")).toBeVisible(),
  );
  expect(
    screen.queryByRole("button", { name: "Submit for review" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Disable chunk" })[0]!);
  expect(
    screen.getAllByRole("button", { name: "Edit chunk" })[0],
  ).toBeDisabled();
  view.unmount();
  render(<Harness />);
  fireEvent.click(screen.getByText("Open"));
  expect(screen.getByText("Updated underwriting rule")).toBeVisible();
});
it("creates chunks and searches their content", () => {
  render(<Harness />);
  fireEvent.click(screen.getByText("Open"));
  fireEvent.click(screen.getByRole("button", { name: "Add chunk" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Chunk content" }), {
    target: { value: "Unique searchable content" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save chunk" }));
  fireEvent.change(screen.getByRole("searchbox", { name: "Search chunks" }), {
    target: { value: "Unique" },
  });
  expect(
    within(screen.getByRole("region", { name: "Chunks" })).getAllByRole(
      "article",
    ),
  ).toHaveLength(1);
});
it("configures parent-child chunks and preserves children when requested", () => {
  function Upload() {
    const review = useDocumentReview("en");
    return (
      <>
        <button onClick={() => review.upload("new.md")}>Upload</button>
        <DocumentReview review={review} onUse={() => {}} />
      </>
    );
  }
  render(<Upload />);
  fireEvent.click(screen.getByText("Upload"));
  fireEvent.change(screen.getByLabelText("Chunk mode"), {
    target: { value: "parent-child" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Preview chunks" }));
  fireEvent.click(screen.getByRole("button", { name: "Save and process" }));
  fireEvent.click(screen.getByRole("button", { name: "Process document" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Edit chunk" })[0]!);
  fireEvent.click(
    screen.getByLabelText("Regenerate child chunks from the updated parent"),
  );
  fireEvent.change(screen.getByLabelText("Chunk content"), {
    target: { value: "Changed parent only" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save chunk" }));
  const first = screen
    .getByRole("region", { name: "Chunks" })
    .querySelector("article")!;
  expect(first.querySelector(".tap-chunk-child")).toHaveTextContent(
    "The application records",
  );
  fireEvent.click(
    within(first).getAllByRole("button", { name: "Edit child" })[0]!,
  );
  fireEvent.change(screen.getByLabelText("Chunk content"), {
    target: { value: "Changed child only" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save chunk" }));
  expect(first).toHaveTextContent("Changed parent only");
  expect(first).toHaveTextContent("Changed child only");
});
it("batch disables then deletes selected chunks with explicit confirmation", () => {
  render(<Harness />);
  fireEvent.click(screen.getByText("Open"));
  fireEvent.click(screen.getByLabelText("Select page"));
  fireEvent.click(screen.getByRole("button", { name: "Disable selected" }));
  for (const button of screen.getAllByRole("button", { name: "Edit chunk" }))
    expect(button).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Select page"));
  fireEvent.click(screen.getByRole("button", { name: "Delete selected" }));
  expect(screen.getByRole("alertdialog")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  expect(
    screen.getByText("No matching chunks. Change your filters or add a chunk."),
  ).toBeVisible();
});
it("fixes the collection mode after processing and keeps full-document parent read-only", () => {
  function Upload() {
    const review = useDocumentReview("en");
    return (
      <>
        <button onClick={() => review.upload("full.md")}>Upload</button>
        <DocumentReview review={review} onUse={() => {}} />
      </>
    );
  }
  render(<Upload />);
  fireEvent.click(screen.getByText("Upload"));
  fireEvent.change(screen.getByLabelText("Chunk mode"), {
    target: { value: "parent-child" },
  });
  fireEvent.change(screen.getByLabelText("Parent context"), {
    target: { value: "full-doc" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Preview chunks" }));
  fireEvent.click(screen.getByRole("button", { name: "Save and process" }));
  fireEvent.click(screen.getByRole("button", { name: "Process document" }));
  expect(screen.getByRole("button", { name: "Edit chunk" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Chunk settings" }));
  expect(screen.getByLabelText("Chunk mode")).toBeDisabled();
});
afterEach(() => vi.useRealTimers());
it("retries indexing a failed chunk", () => {
  vi.useFakeTimers();
  render(
    <ChunkManager
      id="underwriting-evidence-pdf"
      t={(en) => en}
      originalView={null}
      onAvailability={() => {}}
    />,
  );
  expect(screen.getAllByText("Index failed")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Retry indexing" }));
  expect(screen.getByText("Indexing…")).toBeVisible();
  act(() => {
    vi.advanceTimersByTime(800);
  });
  expect(screen.queryByText("Index failed")).not.toBeInTheDocument();
  expect(screen.queryByText("Indexing…")).not.toBeInTheDocument();
});
it("loads saved chunks without index state", () => {
  localStorage.setItem(
    "tap.prototype.chunks.v1.underwriting-evidence-pdf",
    JSON.stringify({
      settings: {
        mode: "general",
        parent: "paragraph",
        delimiter: "\\n\\n",
        max: 500,
        overlap: 50,
        childDelimiter: "\\n",
        childMax: 200,
        whitespace: true,
        removeLinks: false,
      },
      chunks: [
        {
          id: "a",
          content: "First chunk",
          enabled: true,
          edited: false,
          children: [],
        },
        {
          id: "b",
          content: "Second chunk",
          enabled: true,
          edited: false,
          children: [],
        },
      ],
    }),
  );
  render(
    <ChunkManager
      id="underwriting-evidence-pdf"
      t={(en) => en}
      originalView={null}
      onAvailability={() => {}}
    />,
  );
  expect(screen.queryByText("Index failed")).not.toBeInTheDocument();
});
it("keeps a new upload unavailable until chunk settings are processed", () => {
  function Upload() {
    const review = useDocumentReview("en");
    return (
      <>
        <button onClick={() => review.upload("new.md")}>Upload</button>
        <DocumentReview review={review} onUse={() => {}} />
      </>
    );
  }
  render(<Upload />);
  fireEvent.click(screen.getByText("Upload"));
  expect(screen.getByRole("button", { name: "Ask Tapper" })).toBeDisabled();
});
