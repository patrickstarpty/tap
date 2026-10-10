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
  | "graph-read"
  | "runtime-discovery"
  | "source-detail"
  | "outside-allowlist";

interface ClassifiedRequest {
  exactDocumentDetail: boolean;
  exactDocumentList: boolean;
  exactGraphMutation: boolean;
  exactGraphRead: boolean;
  exactRuntimeDiscovery: boolean;
  exactTask9Read: boolean;
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
      exactGraphMutation: false,
      exactGraphRead: false,
      exactRuntimeDiscovery: false,
      exactTask9Read: false,
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
  const projectPath = `/api/v1/projects/${encodeURIComponent(projectId)}`;
  const sourcePath = `${projectPath}/knowledge/sources`;
  const exactSourceRead =
    parsed.pathname.startsWith(`${sourcePath}/`) &&
    /^src_[0-9a-f]{32}$/u.test(parsed.pathname.slice(sourcePath.length + 1)) &&
    parsed.search === "?limit=50";
  const conversationPath = `${projectPath}/conversations`;
  const conversationSuffix = parsed.pathname.slice(conversationPath.length + 1);
  const exactConversationRead =
    parsed.pathname.startsWith(`${conversationPath}/`) &&
    /^(?:[0-9a-f]{32})(?:\/(?:events|stream|turns\/[0-9a-f]{32}\/trace))?$/u.test(
      conversationSuffix,
    ) &&
    parsed.search === "";
  const exactTask9Read =
    (parsed.pathname === sourcePath && parsed.search === "?limit=50") ||
    exactSourceRead ||
    (parsed.pathname === `${projectPath}/knowledge/published-sources` &&
      parsed.search === "") ||
    ([`${projectPath}/ai/agents`, `${projectPath}/ai/skills`].includes(
      parsed.pathname,
    ) &&
      parsed.search === "") ||
    (parsed.pathname === conversationPath && parsed.search === "?limit=20") ||
    exactConversationRead;
  const graphProjectPath = `${projectPath}/knowledge/graph/project`;
  const exactGraphProjectRead =
    parsed.pathname === graphProjectPath && parsed.search === "";
  const graphOverviewPath = `${projectPath}/knowledge/graph/overview`;
  const overviewRevisionIds = parsed.searchParams.getAll("sourceRevisionId");
  const overviewNodeLimit = parsed.searchParams.get("nodeLimit");
  const overviewGraphVersion = parsed.searchParams.get("graphVersion");
  const exactGraphOverviewRead =
    parsed.pathname === graphOverviewPath &&
    [...parsed.searchParams.keys()].every(
      (key) =>
        key === "sourceRevisionId" ||
        key === "communityId" ||
        key === "nodeLimit" ||
        key === "graphVersion",
    ) &&
    overviewRevisionIds.every((revisionId) =>
      /^rev_[0-9a-f]{64}$/u.test(revisionId),
    ) &&
    (overviewNodeLimit === null ||
      (/^[0-9]+$/u.test(overviewNodeLimit) &&
        Number(overviewNodeLimit) >= 1 &&
        Number(overviewNodeLimit) <= 500)) &&
    (overviewGraphVersion === null ||
      /^[1-9][0-9]*$/u.test(overviewGraphVersion));
  const graphNodesPath = `${projectPath}/knowledge/graph/nodes`;
  const graphNodeMatch =
    parsed.pathname.startsWith(`${graphNodesPath}/`) &&
    /^[A-Za-z0-9_:-]{1,128}$/u.test(
      parsed.pathname.slice(graphNodesPath.length + 1),
    );
  const exactGraphNodeRead =
    graphNodeMatch &&
    [...parsed.searchParams.keys()].every((key) => key === "graphVersion");
  const exactGraphRead =
    exactGraphProjectRead || exactGraphOverviewRead || exactGraphNodeRead;
  const exactGraphMutation =
    parsed.search === "" &&
    [
      `${projectPath}/knowledge/graph/query`,
      `${projectPath}/knowledge/graph/highlight`,
      `${projectPath}/knowledge/graph/neighbors`,
      `${projectPath}/knowledge/graph/path`,
    ].includes(parsed.pathname);
  return {
    exactRuntimeDiscovery: runtimePath && parsed.search === "",
    exactDocumentDetail: detailPath && parsed.search === "",
    exactDocumentList: listPath && parsed.search === "?limit=50",
    exactGraphMutation,
    exactGraphRead,
    exactTask9Read,
    label: listPath
      ? "document-list"
      : detailPath
        ? "document-detail"
        : runtimePath
          ? "runtime-discovery"
          : exactGraphRead
            ? "graph-read"
            : exactSourceRead
              ? "source-detail"
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
        classified.exactRuntimeDiscovery ||
        classified.exactGraphRead ||
        classified.exactTask9Read);
    const approvedDeleteCancellation =
      classified.method === "DELETE" &&
      classified.exactDocumentDetail &&
      this.#completedNoContentDeletes.delete(request);
    const approvedPostCancellation =
      classified.method === "POST" && classified.exactGraphMutation;
    if (
      failure.errorText === "net::ERR_ABORTED" &&
      (approvedGetCancellation ||
        approvedDeleteCancellation ||
        approvedPostCancellation)
    ) {
      return null;
    }
    const errorText = SAFE_BROWSER_ERROR.test(failure.errorText)
      ? failure.errorText
      : "unrecognized-browser-error";
    return `${classified.method} ${classified.label} ${errorText}`;
  }
}
