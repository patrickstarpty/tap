import createOpenApiClient from "openapi-fetch";

import problemRegistry from "../../../../../../contracts/problem-types.json";

import type { paths } from "../../../shared/api/generated/schema";
import type {
  DocumentAccepted,
  KnowledgeClient,
  ProblemDetails,
} from "./types";

const DOCUMENT_PATH = "/api/v1/projects/{project_id}/knowledge/documents";
const SOURCE_PATH = "/api/v1/projects/{project_id}/knowledge/sources";

const MEDIA_TYPES_BY_EXTENSION: Readonly<Record<string, string>> = {
  ".docx":
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".markdown": "text/markdown",
  ".md": "text/markdown",
  ".pdf": "application/pdf",
  ".txt": "text/plain",
};

interface KnowledgeClientOptions {
  projectId: string;
  baseUrl?: string;
  fetch?: (input: Request) => Promise<Response>;
  xhrFactory?: () => XMLHttpRequest;
}

export class KnowledgeClientError extends Error {
  readonly code: string;
  readonly status: number;
  readonly correlationId: string;
  readonly retryable: boolean;
  readonly failureStage: ProblemDetails["failureStage"];

  constructor(problem: ProblemDetails) {
    const code = problem.type.slice(problem.type.lastIndexOf("/") + 1);
    super(`Knowledge request failed (${code}).`);
    this.name = "KnowledgeClientError";
    this.code = code;
    this.status = problem.status;
    this.correlationId = problem.correlationId;
    this.retryable = problem.retryable;
    this.failureStage = problem.failureStage;
  }
}

export class KnowledgeTransportError extends Error {
  readonly code: "invalid-problem" | "network-failure";
  declare readonly status?: number;

  constructor(code: "invalid-problem" | "network-failure", status?: number) {
    super(`Knowledge request failed (${code}).`);
    this.name = "KnowledgeTransportError";
    this.code = code;
    if (status !== undefined) this.status = status;
  }
}

function responseError(
  value: unknown,
  status: number,
  mediaType: string | null,
): Error {
  const invalid = () => new KnowledgeTransportError("invalid-problem", status);
  if (
    mediaType?.split(";", 1)[0]?.trim().toLowerCase() !==
      "application/problem+json" ||
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value)
  )
    return invalid();
  const candidate = value as Record<string, unknown>;
  const definition = problemRegistry.problems.find(
    (item) => item.type === candidate.type,
  );
  if (
    definition === undefined ||
    candidate.status !== status ||
    definition.status !== status ||
    candidate.title !== definition.title ||
    candidate.detail !== definition.detail ||
    candidate.retryable !== definition.retryable ||
    (candidate.failureStage ?? undefined) !== definition.failureStage ||
    typeof candidate.correlationId !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(candidate.correlationId) ||
    (candidate.instance !== undefined &&
      candidate.instance !== null &&
      (typeof candidate.instance !== "string" ||
        candidate.instance.length === 0 ||
        candidate.instance.length > 2048)) ||
    Object.keys(candidate).some(
      (key) =>
        ![
          "type",
          "title",
          "status",
          "detail",
          "instance",
          "correlationId",
          "retryable",
          "failureStage",
        ].includes(key),
    )
  )
    return invalid();
  return new KnowledgeClientError(candidate as unknown as ProblemDetails);
}

function origin(): string {
  if (typeof window !== "undefined" && window.location.origin !== "null") {
    return window.location.origin;
  }
  return "http://127.0.0.1";
}

export function resolveApiBaseUrl(baseUrl = ""): string {
  const resolved = new URL(
    baseUrl.length === 0 ? "/" : baseUrl,
    `${origin()}/`,
  );
  return resolved.toString().replace(/\/$/u, "");
}

function requestUrl(baseUrl: string, path: string): string {
  return `${baseUrl}${path}`;
}

function canonicalUploadFile(file: File): File {
  const lowerName = file.name.toLowerCase();
  const extension = Object.keys(MEDIA_TYPES_BY_EXTENSION).find((candidate) =>
    lowerName.endsWith(candidate),
  );
  const mediaType =
    extension === undefined ? file.type : MEDIA_TYPES_BY_EXTENSION[extension];
  if (mediaType === undefined || mediaType === file.type) return file;
  return new File([file], file.name, {
    lastModified: file.lastModified,
    type: mediaType,
  });
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return undefined;
  }
}

export function createKnowledgeClient(
  options: KnowledgeClientOptions,
): KnowledgeClient {
  const projectId = options.projectId;
  if (typeof projectId !== "string" || projectId.trim().length === 0) {
    throw new Error("A project ID is required for Knowledge requests.");
  }
  const baseUrl = resolveApiBaseUrl(options.baseUrl);
  const http = createOpenApiClient<paths>({
    baseUrl,
    ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
  });
  http.use({
    async onResponse({ response }) {
      if (!response.ok) {
        let body: unknown;
        try {
          body = parseJson(await response.text());
        } catch (error) {
          if (
            (error instanceof DOMException || error instanceof Error) &&
            error.name === "AbortError"
          )
            throw error;
          throw new KnowledgeTransportError("network-failure");
        }
        throw responseError(
          body,
          response.status,
          response.headers.get("content-type"),
        );
      }
    },
    onError({ error }) {
      if (
        (error instanceof DOMException || error instanceof Error) &&
        error.name === "AbortError"
      )
        throw error;
      return new KnowledgeTransportError("network-failure");
    },
  });
  const xhrFactory = options.xhrFactory ?? (() => new XMLHttpRequest());

  return {
    projectId,
    async listSources({ cursor, limit, signal }) {
      const result = await http.GET(SOURCE_PATH, {
        params: { path: { project_id: projectId }, query: { cursor, limit } },
        signal,
      });
      return result.data!;
    },
    async getSource(sourceId, signal) {
      const result = await http.GET(
        "/api/v1/projects/{project_id}/knowledge/sources/{source_id}",
        {
          params: {
            path: { project_id: projectId, source_id: sourceId },
            query: { limit: 50 },
          },
          signal,
        },
      );
      return result.data!;
    },
    async uploadSource(
      file,
      onProgress,
      signal,
      idempotencyKey = crypto.randomUUID(),
    ) {
      onProgress(0);
      const form = new FormData();
      form.append("upload", canonicalUploadFile(file));
      const result = await http.POST(SOURCE_PATH, {
        params: {
          path: { project_id: projectId },
          header: { "idempotency-key": idempotencyKey },
        },
        body: { upload: file.name },
        bodySerializer: () => form,
        signal,
      });
      onProgress(1);
      return result.data!;
    },
    async retrySource(sourceId, body, idempotencyKey = crypto.randomUUID()) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/knowledge/sources/{source_id}/retry",
        {
          params: {
            path: { project_id: projectId, source_id: sourceId },
            header: { "idempotency-key": idempotencyKey },
          },
          body,
        },
      );
      return result.data!;
    },
    async deleteSource(sourceId, idempotencyKey = crypto.randomUUID()) {
      await http.DELETE(
        "/api/v1/projects/{project_id}/knowledge/sources/{source_id}",
        {
          params: {
            path: { project_id: projectId, source_id: sourceId },
            header: { "idempotency-key": idempotencyKey },
          },
        },
      );
    },
    async listDocuments({ cursor, limit, signal }) {
      const result = await http.GET(DOCUMENT_PATH, {
        params: { path: { project_id: projectId }, query: { cursor, limit } },
        signal,
      });
      if (result.error !== undefined) {
        throw responseError(
          result.error,
          result.response.status,
          result.response.headers.get("content-type"),
        );
      }
      return result.data;
    },

    async getDocument(documentId, signal) {
      const result = await http.GET(
        "/api/v1/projects/{project_id}/knowledge/documents/{document_id}",
        {
          params: { path: { project_id: projectId, document_id: documentId } },
          signal,
        },
      );
      if (result.error !== undefined) {
        throw responseError(
          result.error,
          result.response.status,
          result.response.headers.get("content-type"),
        );
      }
      return result.data;
    },

    uploadDocument(
      file,
      onProgress,
      signal,
      idempotencyKey = crypto.randomUUID(),
    ) {
      return new Promise<DocumentAccepted>((resolve, reject) => {
        const request = xhrFactory();
        let settled = false;

        const finish = (operation: () => void) => {
          if (settled) return;
          settled = true;
          signal?.removeEventListener("abort", abort);
          operation();
        };
        const abort = () => request.abort();

        request.open(
          "POST",
          requestUrl(
            baseUrl,
            DOCUMENT_PATH.replace(
              "{project_id}",
              encodeURIComponent(projectId),
            ),
          ),
        );
        request.setRequestHeader("Idempotency-Key", idempotencyKey);
        request.upload.onprogress = (event) => {
          if (event.lengthComputable && event.total > 0) {
            onProgress(Math.min(1, Math.max(0, event.loaded / event.total)));
          }
        };
        request.onload = () => {
          const body = parseJson(request.responseText);
          if (request.status === 202) {
            finish(() => resolve(body as DocumentAccepted));
            return;
          }
          finish(() =>
            reject(
              responseError(
                body,
                request.status,
                request.getResponseHeader("content-type"),
              ),
            ),
          );
        };
        request.onerror = () => {
          finish(() => reject(new KnowledgeTransportError("network-failure")));
        };
        request.onabort = () => {
          finish(() =>
            reject(new DOMException("Upload aborted", "AbortError")),
          );
        };

        signal?.addEventListener("abort", abort, { once: true });
        if (signal?.aborted === true) {
          abort();
          return;
        }

        const form = new FormData();
        form.append("upload", canonicalUploadFile(file));
        request.send(form);
      });
    },

    async retryDocument(documentId, idempotencyKey = crypto.randomUUID()) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/knowledge/documents/{document_id}/retry",
        {
          params: {
            path: { project_id: projectId, document_id: documentId },
            header: { "idempotency-key": idempotencyKey },
          },
        },
      );
      if (result.error !== undefined) {
        throw responseError(
          result.error,
          result.response.status,
          result.response.headers.get("content-type"),
        );
      }
      return result.data;
    },

    async deleteDocument(documentId, idempotencyKey = crypto.randomUUID()) {
      const result = await http.DELETE(
        "/api/v1/projects/{project_id}/knowledge/documents/{document_id}",
        {
          params: {
            path: { project_id: projectId, document_id: documentId },
            header: { "idempotency-key": idempotencyKey },
          },
        },
      );
      if (result.error !== undefined) {
        throw responseError(
          result.error,
          result.response.status,
          result.response.headers.get("content-type"),
        );
      }
    },

    async createAnswer(request, signal) {
      const result = await http.POST(
        "/api/v1/projects/{project_id}/knowledge/answers",
        {
          params: { path: { project_id: projectId } },
          body: request,
          signal,
        },
      );
      if (result.error !== undefined) {
        throw responseError(
          result.error,
          result.response.status,
          result.response.headers.get("content-type"),
        );
      }
      return result.data;
    },

    async getCitation(citationId, signal) {
      const result = await http.GET(
        "/api/v1/projects/{project_id}/knowledge/citations/{citation_id}",
        {
          params: { path: { project_id: projectId, citation_id: citationId } },
          signal,
        },
      );
      if (result.error !== undefined) {
        throw responseError(
          result.error,
          result.response.status,
          result.response.headers.get("content-type"),
        );
      }
      return result.data;
    },
  };
}
