const APPROVED_ORIGIN = "http://127.0.0.1:15173";
const DOCUMENT_ID = "doc_[0-9a-f]{32}";
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
  | "document-detail"
  | "document-list"
  | "runtime-discovery"
  | "outside-allowlist";

interface ClassifiedRequest {
  exactDocumentDetail: boolean;
  exactDocumentList: boolean;
  exactRuntimeDiscovery: boolean;
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

function classifyRequest(
  failure: E2ERequestFailure,
  projectId: string,
): ClassifiedRequest {
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
      exactRuntimeDiscovery: false,
      label: "outside-allowlist",
      method,
    };
  }
  const documentListPath = `/api/v1/projects/${encodeURIComponent(projectId)}/knowledge/documents`;
  const listPath = parsed.pathname === documentListPath;
  const detailPath =
    parsed.pathname.startsWith(`${documentListPath}/`) &&
    new RegExp(`^${DOCUMENT_ID}$`, "u").test(
      parsed.pathname.slice(documentListPath.length + 1),
    );
  const runtimePath = parsed.pathname === "/api/v1/runtime-mode";
  return {
    exactRuntimeDiscovery: runtimePath && parsed.search === "",
    exactDocumentDetail: detailPath && parsed.search === "",
    exactDocumentList: listPath && parsed.search === "?limit=50",
    label: listPath
      ? "document-list"
      : detailPath
        ? "document-detail"
        : runtimePath
          ? "runtime-discovery"
          : "outside-allowlist",
    method,
  };
}

export class E2ERequestFailureAudit<RequestIdentity extends object> {
  constructor(private readonly projectId: string) {}

  readonly #completedNoContentDeletes = new WeakSet<RequestIdentity>();

  observeResponse(
    request: RequestIdentity,
    response: E2ERequestResponse,
  ): void {
    const classified = classifyRequest(
      {
        errorText: "",
        method: response.method,
        url: response.url,
      },
      this.projectId,
    );
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
    const classified = classifyRequest(failure, this.projectId);
    const approvedGetCancellation =
      classified.method === "GET" &&
      (classified.exactDocumentList ||
        classified.exactDocumentDetail ||
        classified.exactRuntimeDiscovery);
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
