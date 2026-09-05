import { describe, expect, it } from "vitest";

import { createKnowledgeClient, KnowledgeClientError } from "./client";
import type { RetrievalAnswerRequest } from "./types";

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

  it("passes AbortSignal through the generated answer and citation requests", async () => {
    const requests: Request[] = [];
    const fetch = async (request: Request): Promise<Response> => {
      requests.push(request);
      if (request.url.includes("/v1/knowledge/answers")) {
        return Response.json({
          traceId: "trace-a",
          queryPlanId: "plan-a",
          contextSnapshotId: "snapshot-a",
          corpusVersion: "tapper-demo-v1",
          retrievalProfileId: "quick-hybrid-v1",
          degradedMode: false,
          degradationReasons: null,
          answer: "",
          abstained: true,
          abstentionReason: "insufficient_evidence",
          claims: [],
          citations: [],
        });
      }
      return Response.json({
        citationId: "citation-a",
        documentId: "doc-a",
        revisionId: "rev-a",
        filename: "policy.md",
        sourceContentHash: `sha256:${"a".repeat(64)}`,
        chunkContentHash: `sha256:${"b".repeat(64)}`,
        anchor: {
          type: "document",
          headingPath: ["Policy"],
          page: 1,
          bbox: null,
          startOffset: 0,
          endOffset: 8,
        },
        quote: "Evidence",
        prefix: "",
        suffix: "",
      });
    };
    const client = createKnowledgeClient({ fetch });
    const controller = new AbortController();
    const answerRequest: RetrievalAnswerRequest = {
      query: "退款需要几人审批？",
      answerMode: "quick",
      sources: ["doc"],
      resourceRefs: [{ family: "doc", sourceId: "doc-a", mode: "scope" }],
    };

    await client.createAnswer(answerRequest, controller.signal);
    await client.getCitation("citation-a", controller.signal);
    controller.abort();

    expect(requests).toHaveLength(2);
    expect(requests.every((request) => request.signal.aborted)).toBe(true);
    await expect(requests[0]?.json()).resolves.toEqual(answerRequest);
  });

  it("uses the HTTP status as authority when a Problem body claims another status", async () => {
    const fetch = async (): Promise<Response> =>
      Response.json(
        {
          type: "https://tap.example/problems/citation-stale",
          title: "Citation stale",
          status: 404,
          detail: "provider secret",
        },
        {
          status: 503,
          headers: { "content-type": "application/problem+json" },
        },
      );
    const client = createKnowledgeClient({ fetch });

    const error = await client
      .getCitation("citation-a")
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(KnowledgeClientError);
    expect(error).toMatchObject({ code: "request-failed", status: 503 });
    expect(String(error)).not.toContain("provider secret");
  });
});
