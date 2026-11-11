import { useState } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DefinitionEditor } from "./DefinitionEditor";
import { createLcaClient, type LcaDefinition } from "./client";
const asset = {
  id: "asset-1",
  filename: "invoice.csv",
  content_type: "application/octet-stream",
  size_bytes: 5,
  sha256: "a".repeat(64),
  created_at: "2026-11-11T12:00:00Z",
};
const definition: LcaDefinition = {
  schema_version: "lca-debug-v1",
  allowed_origins: ["https://example.com"],
  targets: [{ id: "input", kind: "test_id", value: "upload" }],
  parameters: [],
  steps: [
    {
      id: "upload",
      operation: "upload_file",
      target_id: "input",
      arguments: { asset_id: "", sha256: "" },
      timeout_ms: 10000,
    },
  ],
};
function Editor({
  fetcher,
  initial = definition,
}: {
  fetcher: typeof fetch;
  initial?: LcaDefinition;
}) {
  const [value, setValue] = useState(initial);
  const [client] = useState(() => createLcaClient(fetcher));
  return (
    <>
      <DefinitionEditor
        value={value}
        onChange={setValue}
        projectId="project a"
        client={client}
      />
      <output data-testid="definition">{JSON.stringify(value)}</output>
    </>
  );
}
const current = () =>
  JSON.parse(screen.getByTestId("definition").textContent!).steps[0];

it("selects only a project asset and pins its digest without entering paths", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify([asset])));
  render(<Editor fetcher={fetcher} />);
  await screen.findByRole("option", { name: "invoice.csv (5 bytes)" });
  fireEvent.change(screen.getByLabelText("Step upload project file"), {
    target: { value: asset.id },
  });
  expect(current().arguments).toEqual({
    asset_id: asset.id,
    sha256: asset.sha256,
  });
  expect(fetcher.mock.calls[0][0]).toBe("/api/projects/project%20a/lca/assets");
  expect(screen.getByText(/SHA-256:/)).toHaveTextContent(asset.sha256);
  expect(screen.queryByLabelText(/path/i)).not.toBeInTheDocument();
});

it("requires explicit upload and sends selected bounded file bytes once with an idempotency key", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((_url, init) =>
      Promise.resolve(
        new Response(JSON.stringify(init?.method === "POST" ? asset : [])),
      ),
    );
  render(<Editor fetcher={fetcher} />);
  await screen.findByText("No project files loaded.");
  fireEvent.change(screen.getByLabelText("Choose project file"), {
    target: {
      files: [new File(["hello"], "invoice.csv", { type: "text/csv" })],
    },
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
  const button = screen.getByRole("button", { name: "Upload selected file" });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() =>
    expect(current().arguments).toEqual({
      asset_id: asset.id,
      sha256: asset.sha256,
    }),
  );
  const [, request] = fetcher.mock.calls[1];
  expect(request.method).toBe("POST");
  expect(request.credentials).toBe("same-origin");
  expect(JSON.parse(request.body)).toEqual({
    filename: "invoice.csv",
    content_base64: "aGVsbG8=",
  });
  expect(new Headers(request.headers).get("Idempotency-Key")).toBeTruthy();
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("rejects an oversized or empty file before reading or uploading it", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("[]"));
  render(<Editor fetcher={fetcher} />);
  await screen.findByText("No project files loaded.");
  fireEvent.change(screen.getByLabelText("Choose project file"), {
    target: { files: [new File([new Uint8Array(256001)], "large.txt")] },
  });
  expect(screen.getByRole("alert")).toHaveTextContent("256,000");
  expect(
    screen.getByRole("button", { name: "Upload selected file" }),
  ).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Choose project file"), {
    target: { files: [new File([], "empty.txt")] },
  });
  expect(screen.getByRole("alert")).toHaveTextContent("empty");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("preserves unavailable or changed asset bindings until the user explicitly replaces them", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response(JSON.stringify([asset])));
  render(
    <Editor
      fetcher={fetcher}
      initial={{
        ...definition,
        steps: [
          {
            ...definition.steps[0],
            arguments: { asset_id: asset.id, sha256: "b".repeat(64) },
          },
        ],
      }}
    />,
  );
  await screen.findByRole("option", { name: "invoice.csv (5 bytes)" });
  expect(screen.getByRole("alert")).toHaveTextContent("digest");
  expect(current().arguments.sha256).toBe("b".repeat(64));
});

it("does not apply a late upload to a different selected operation", async () => {
  let finish!: (response: Response) => void;
  const fetcher = vi.fn().mockImplementation((_url, init) =>
    init?.method === "POST"
      ? new Promise<Response>((resolve) => {
          finish = resolve;
        })
      : Promise.resolve(new Response("[]")),
  );
  render(<Editor fetcher={fetcher} />);
  await screen.findByText("No project files loaded.");
  fireEvent.change(screen.getByLabelText("Choose project file"), {
    target: { files: [new File(["hello"], "invoice.csv")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload selected file" }));
  await waitFor(() => expect(finish).toBeTypeOf("function"));
  fireEvent.change(screen.getByLabelText("Step upload operation"), {
    target: { value: "click" },
  });
  finish(new Response(JSON.stringify(asset)));
  await waitFor(() => expect(current().operation).toBe("click"));
  expect(current().arguments).toEqual({});
});

it("reconciles an uncertain upload with the original bytes and idempotency key", async () => {
  let attempts = 0;
  const fetcher = vi.fn().mockImplementation((_url, init) => {
    if (init?.method !== "POST") return Promise.resolve(new Response("[]"));
    attempts += 1;
    return attempts === 1
      ? Promise.reject(new TypeError("connection lost"))
      : Promise.resolve(new Response(JSON.stringify(asset)));
  });
  render(<Editor fetcher={fetcher} />);
  await screen.findByText("No project files loaded.");
  fireEvent.change(screen.getByLabelText("Choose project file"), {
    target: { files: [new File(["hello"], "invoice.csv")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload selected file" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "could not be confirmed",
  );
  expect(current().arguments).toEqual({ asset_id: "", sha256: "" });
  expect(screen.getByLabelText("Choose project file")).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "Reconcile file upload" }),
  );
  await waitFor(() => expect(current().arguments.asset_id).toBe(asset.id));
  expect(fetcher.mock.calls[2][1].body).toBe(fetcher.mock.calls[1][1].body);
  expect(
    new Headers(fetcher.mock.calls[2][1].headers).get("Idempotency-Key"),
  ).toBe(new Headers(fetcher.mock.calls[1][1].headers).get("Idempotency-Key"));
});

it("lets the user replace a definitively rejected upload without trapping the file picker", async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((_url, init) =>
      Promise.resolve(
        init?.method === "POST"
          ? new Response("invalid", { status: 422 })
          : new Response("[]"),
      ),
    );
  render(<Editor fetcher={fetcher} />);
  await screen.findByText("No project files loaded.");
  fireEvent.change(screen.getByLabelText("Choose project file"), {
    target: { files: [new File(["hello"], "invoice.csv")] },
  });
  fireEvent.click(screen.getByRole("button", { name: "Upload selected file" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("not been bound");
  expect(screen.getByLabelText("Choose project file")).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: "Reconcile file upload" }),
  ).not.toBeInTheDocument();
});
