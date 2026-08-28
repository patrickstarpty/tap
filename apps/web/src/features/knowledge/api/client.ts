import createOpenApiClient from "openapi-fetch";

import type { paths } from "../../../shared/api/generated/schema";
import type {
  DocumentAccepted,
  KnowledgeClient,
  ProblemDetails,
} from "./types";

const DOCUMENT_PATH = "/v1/knowledge/documents";

const MEDIA_TYPES_BY_EXTENSION: Readonly<Record<string, string>> = {
  ".docx":
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".markdown": "text/markdown",
  ".md": "text/markdown",
  ".pdf": "application/pdf",
  ".txt": "text/plain",
};

interface KnowledgeClientOptions {
  baseUrl?: string;
  fetch?: (input: Request) => Promise<Response>;
  xhrFactory?: () => XMLHttpRequest;
}

function problemCode(type: string): string {
  const withoutQuery = type.split(/[?#]/u, 1)[0] ?? "";
  const segments = withoutQuery.split("/").filter(Boolean);
  const code = segments.at(-1);
  return code === undefined || code.includes(":") ? "request-failed" : code;
}

export class KnowledgeClientError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(problem: ProblemDetails) {
    const code = problemCode(problem.type);
    super(`Knowledge request failed (${code}).`);
    this.name = "KnowledgeClientError";
    this.code = code;
    this.status = problem.status;
  }
}

function fallbackProblem(status: number): ProblemDetails {
  return {
    detail: "",
    status,
    title: "Request failed",
    type: "about:blank",
  };
}

function asProblemDetails(value: unknown, status: number): ProblemDetails {
  if (typeof value !== "object" || value === null)
    return fallbackProblem(status);
  const candidate = value as Partial<ProblemDetails>;
  if (
    typeof candidate.type !== "string" ||
    typeof candidate.title !== "string" ||
    typeof candidate.status !== "number" ||
    candidate.status !== status ||
    typeof candidate.detail !== "string"
  ) {
    return fallbackProblem(status);
  }
  return candidate as ProblemDetails;
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
  options: KnowledgeClientOptions = {},
): KnowledgeClient {
  const baseUrl = resolveApiBaseUrl(options.baseUrl);
  const http = createOpenApiClient<paths>({
    baseUrl,
    ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
  });
  const xhrFactory = options.xhrFactory ?? (() => new XMLHttpRequest());

  return {
    async listDocuments({ cursor, limit, signal }) {
      const result = await http.GET(DOCUMENT_PATH, {
        params: { query: { cursor, limit } },
        signal,
      });
      if (result.error !== undefined) {
        throw new KnowledgeClientError(
          asProblemDetails(result.error, result.response.status),
        );
      }
      return result.data;
    },

    async getDocument(documentId, signal) {
      const result = await http.GET("/v1/knowledge/documents/{document_id}", {
        params: { path: { document_id: documentId } },
        signal,
      });
      if (result.error !== undefined) {
        throw new KnowledgeClientError(
          asProblemDetails(result.error, result.response.status),
        );
      }
      return result.data;
    },

    uploadDocument(file, onProgress, signal) {
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

        request.open("POST", requestUrl(baseUrl, DOCUMENT_PATH));
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
              new KnowledgeClientError(
                asProblemDetails(body, request.status || 500),
              ),
            ),
          );
        };
        request.onerror = () => {
          finish(() => reject(new KnowledgeClientError(fallbackProblem(503))));
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

    async retryDocument(documentId) {
      const result = await http.POST(
        "/v1/knowledge/documents/{document_id}/retry",
        { params: { path: { document_id: documentId } } },
      );
      if (result.error !== undefined) {
        throw new KnowledgeClientError(
          asProblemDetails(result.error, result.response.status),
        );
      }
      return result.data;
    },

    async deleteDocument(documentId) {
      const result = await http.DELETE(
        "/v1/knowledge/documents/{document_id}",
        { params: { path: { document_id: documentId } } },
      );
      if (result.error !== undefined) {
        throw new KnowledgeClientError(
          asProblemDetails(result.error, result.response.status),
        );
      }
    },

    async createAnswer(request, signal) {
      const result = await http.POST("/v1/knowledge/answers", {
        body: request,
        signal,
      });
      if (result.error !== undefined) {
        throw new KnowledgeClientError(
          asProblemDetails(result.error, result.response.status),
        );
      }
      return result.data;
    },

    async getCitation(citationId, signal) {
      const result = await http.GET("/v1/citations/{citation_id}", {
        params: { path: { citation_id: citationId } },
        signal,
      });
      if (result.error !== undefined) {
        throw new KnowledgeClientError(
          asProblemDetails(result.error, result.response.status),
        );
      }
      return result.data;
    },
  };
}
