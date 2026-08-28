import { describe, expect, it } from "vitest";

import { createKnowledgeClient, KnowledgeClientError } from "./client";

class TestUploadRequest {
  readonly upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  };
  method = "";
  url = "";
  body: Document | XMLHttpRequestBodyInit | null = null;
  status = 0;
  responseText = "";
  aborted = false;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;

  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }

  send(body: Document | XMLHttpRequestBodyInit | null): void {
    this.body = body;
  }

  abort(): void {
    this.aborted = true;
    this.onabort?.();
  }

  respond(status: number, body: unknown): void {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }
}

describe("KnowledgeClient", () => {
  it("uses the generated list route and returns its typed document page", async () => {
    const requests: Request[] = [];
    const fetch = async (request: Request): Promise<Response> => {
      requests.push(request);
      return Response.json({ items: [], nextCursor: null });
    };
    const client = createKnowledgeClient({ baseUrl: "/api", fetch });

    await expect(client.listDocuments({ limit: 25 })).resolves.toEqual({
      items: [],
      nextCursor: null,
    });
    expect(requests).toHaveLength(1);
    expect(requests[0]?.method).toBe("GET");
    expect(
      requests[0]?.url.endsWith("/api/v1/knowledge/documents?limit=25"),
    ).toBe(true);
  });

  it("maps Problem Details to a safe client error without exposing detail", async () => {
    const fetch = async (): Promise<Response> =>
      Response.json(
        {
          type: "https://tap.local/problems/document-too-large",
          title: "Provider said /srv/private/key",
          status: 413,
          detail: "secret=sk-live-internal at /srv/private/key",
        },
        {
          status: 413,
          headers: { "content-type": "application/problem+json" },
        },
      );
    const client = createKnowledgeClient({ fetch });

    const error = await client
      .listDocuments({ limit: 25 })
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(KnowledgeClientError);
    expect(error).toMatchObject({ code: "document-too-large", status: 413 });
    expect(String(error)).not.toContain("sk-live-internal");
    expect(String(error)).not.toContain("/srv/private/key");
  });

  it("reports XHR progress and resolves the typed 202 upload receipt", async () => {
    const request = new TestUploadRequest();
    const client = createKnowledgeClient({
      baseUrl: "/api",
      xhrFactory: () => request as unknown as XMLHttpRequest,
    });
    const progress: number[] = [];
    const upload = client.uploadDocument(
      new File(["# Notes"], "notes.md"),
      (ratio) => progress.push(ratio),
    );

    request.upload.onprogress?.(
      new ProgressEvent("progress", {
        lengthComputable: true,
        loaded: 5,
        total: 10,
      }),
    );
    request.respond(202, {
      document: {
        chunkCount: 0,
        documentId: "doc-1",
        errorCode: null,
        errorSummary: null,
        filename: "notes.md",
        mediaType: "text/markdown",
        stage: "stored",
        status: "queued",
        updatedAt: "2026-08-28T07:30:00Z",
      },
      duplicate: false,
      jobId: "job-1",
    });

    await expect(upload).resolves.toMatchObject({
      duplicate: false,
      jobId: "job-1",
    });
    expect(progress).toEqual([0.5]);
    expect(request.method).toBe("POST");
    expect(request.url.endsWith("/api/v1/knowledge/documents")).toBe(true);
    expect(request.body).toBeInstanceOf(FormData);
    expect((request.body as FormData).get("upload")).toMatchObject({
      name: "notes.md",
      type: "text/markdown",
    });
  });

  it("maps a non-202 XHR Problem Details response without exposing provider text", async () => {
    const request = new TestUploadRequest();
    const client = createKnowledgeClient({
      xhrFactory: () => request as unknown as XMLHttpRequest,
    });
    const upload = client.uploadDocument(
      new File(["private"], "notes.txt"),
      () => undefined,
    );

    request.respond(413, {
      type: "https://tap.local/problems/document-too-large",
      title: "Provider failed at /srv/private/key",
      status: 413,
      detail: "secret=sk-live-internal at /srv/private/key",
      instance: "/internal/jobs/provider-secret",
    });
    const error = await upload.catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(KnowledgeClientError);
    expect(error).toMatchObject({ code: "document-too-large", status: 413 });
    expect(String(error)).not.toMatch(
      /sk-live-internal|\/srv\/private|provider-secret/u,
    );
  });

  it("aborts the active XHR when its signal is aborted", async () => {
    const request = new TestUploadRequest();
    const client = createKnowledgeClient({
      xhrFactory: () => request as unknown as XMLHttpRequest,
    });
    const controller = new AbortController();
    const upload = client.uploadDocument(
      new File(["notes"], "notes.txt", { type: "text/plain" }),
      () => undefined,
      controller.signal,
    );

    controller.abort();

    await expect(upload).rejects.toMatchObject({ name: "AbortError" });
    expect(request.aborted).toBe(true);
  });
});
