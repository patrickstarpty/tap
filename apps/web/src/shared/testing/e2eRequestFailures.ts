const APPROVED_ORIGIN = "http://127.0.0.1:15173";
const DOCUMENT_ID = "doc_[0-9a-f]{32}";
const DOCUMENT_PATH = new RegExp(
  `^/v1/knowledge/documents/${DOCUMENT_ID}$`,
  "u",
);
const DOCUMENT_LIST_PATH = "/v1/knowledge/documents";
const SAFE_METHOD = /^[A-Z]{1,16}$/u;
const SAFE_BROWSER_ERROR = /^net::ERR_[A-Z0-9_]{1,64}$/u;

export interface E2ERequestFailure {
  errorText: string;
  method: string;
  url: string;
}

export interface E2ERequestResponse {
  method: string;
  status: number;
  url: string;
}

type ClosedPathLabel =
  "document-detail" | "document-list" | "outside-allowlist";

interface ClassifiedRequest {
  exactDocumentDetail: boolean;
  exactDocumentList: boolean;
  label: ClosedPathLabel;
  method: string;
}

function isApprovedPageUrl(parsed: URL): boolean {
  return (
    parsed.origin === APPROVED_ORIGIN &&
    parsed.username === "" &&
    parsed.password === "" &&
    parsed.hash === ""
  );
}

export function isApprovedE2EPageRequest(url: string): boolean {
  try {
    return isApprovedPageUrl(new URL(url));
  } catch {
    return false;
  }
}

function classifyRequest(failure: E2ERequestFailure): ClassifiedRequest {
  const method = SAFE_METHOD.test(failure.method) ? failure.method : "UNKNOWN";
  let parsed: URL | undefined;
  try {
    parsed = new URL(failure.url);
  } catch {
    parsed = undefined;
  }
  if (parsed === undefined || !isApprovedPageUrl(parsed)) {
    return {
      exactDocumentDetail: false,
      exactDocumentList: false,
      label: "outside-allowlist",
      method,
    };
  }
  const listPath = parsed.pathname === DOCUMENT_LIST_PATH;
  const detailPath = DOCUMENT_PATH.test(parsed.pathname);
  return {
    exactDocumentDetail: detailPath && parsed.search === "",
    exactDocumentList: listPath && parsed.search === "?limit=50",
    label: listPath
      ? "document-list"
      : detailPath
        ? "document-detail"
        : "outside-allowlist",
    method,
  };
}

export class E2ERequestFailureAudit<RequestIdentity extends object> {
  readonly #completedNoContentDeletes = new WeakSet<RequestIdentity>();

  observeResponse(
    request: RequestIdentity,
    response: E2ERequestResponse,
  ): void {
    const classified = classifyRequest({
      errorText: "",
      method: response.method,
      url: response.url,
    });
    if (
      response.status === 204 &&
      classified.method === "DELETE" &&
      classified.exactDocumentDetail
    ) {
      this.#completedNoContentDeletes.add(request);
    }
  }

  unexpectedFailure(
    request: RequestIdentity,
    failure: E2ERequestFailure,
  ): string | null {
    const classified = classifyRequest(failure);
    const approvedGetCancellation =
      classified.method === "GET" &&
      (classified.exactDocumentList || classified.exactDocumentDetail);
    const approvedDeleteCancellation =
      classified.method === "DELETE" &&
      classified.exactDocumentDetail &&
      this.#completedNoContentDeletes.delete(request);
    if (
      failure.errorText === "net::ERR_ABORTED" &&
      (approvedGetCancellation || approvedDeleteCancellation)
    ) {
      return null;
    }
    const errorText = SAFE_BROWSER_ERROR.test(failure.errorText)
      ? failure.errorText
      : "unrecognized-browser-error";
    return `${classified.method} ${classified.label} ${errorText}`;
  }
}
