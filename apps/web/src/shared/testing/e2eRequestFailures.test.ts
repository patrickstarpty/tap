import { describe, expect, it } from "vitest";

import {
  E2ERequestFailureAudit,
  isApprovedE2EPageRequest,
} from "./e2eRequestFailures";

describe("E2ERequestFailureAudit", () => {
  it.each([
    ["http://127.0.0.1:15173/", true],
    [
      "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents?limit=50",
      true,
    ],
    ["http://127.0.0.1:18000/", false],
    ["https://127.0.0.1:15173/", false],
    ["http://provider-secret@127.0.0.1:15173/", false],
    ["http://127.0.0.1:15173/#provider-secret", false],
  ])("classifies the exact page request boundary for %s", (url, approved) => {
    expect(isApprovedE2EPageRequest(url)).toBe(approved);
  });

  it.each([
    [
      "GET",
      "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents?limit=50",
    ],
    [
      "GET",
      "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents/doc_0123456789abcdef0123456789abcdef",
    ],
  ])("ignores only an approved GET cancellation for %s", (method, url) => {
    const request = {};
    const audit = new E2ERequestFailureAudit<object>("project-e2e");

    expect(
      audit.unexpectedFailure(request, {
        errorText: "net::ERR_ABORTED",
        method,
        url,
      }),
    ).toBeNull();
  });

  it("allows only exact runtime discovery GET cancellation", () => {
    const audit = new E2ERequestFailureAudit<object>("project-e2e");
    const url = "http://127.0.0.1:15173/api/v1/runtime-mode";
    expect(
      audit.unexpectedFailure(
        {},
        {
          method: "GET",
          url,
          errorText: "net::ERR_ABORTED",
        },
      ),
    ).toBeNull();
    for (const failure of [
      { method: "POST", url, errorText: "net::ERR_ABORTED" },
      { method: "GET", url, errorText: "net::ERR_FAILED" },
      {
        method: "GET",
        url: `${url}?secret=value`,
        errorText: "net::ERR_ABORTED",
      },
      { method: "GET", url: `${url}#fragment`, errorText: "net::ERR_ABORTED" },
      { method: "GET", url: `${url}/`, errorText: "net::ERR_ABORTED" },
      {
        method: "GET",
        url: url.replace(":15173", ":18000"),
        errorText: "net::ERR_ABORTED",
      },
      {
        method: "GET",
        url: url.replace("http:", "https:"),
        errorText: "net::ERR_ABORTED",
      },
    ])
      expect(audit.unexpectedFailure({}, failure)).not.toBeNull();
  });

  it("ignores an aborted DELETE only after the same request received 204", () => {
    const completedRequest = {};
    const differentRequest = {};
    const url =
      "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents/doc_0123456789abcdef0123456789abcdef";
    const audit = new E2ERequestFailureAudit<object>("project-e2e");
    audit.observeResponse(completedRequest, {
      method: "DELETE",
      status: 204,
      url,
    });

    expect(
      audit.unexpectedFailure(differentRequest, {
        errorText: "net::ERR_ABORTED",
        method: "DELETE",
        url,
      }),
    ).toBe("DELETE document-detail net::ERR_ABORTED");
    expect(
      audit.unexpectedFailure(completedRequest, {
        errorText: "net::ERR_ABORTED",
        method: "DELETE",
        url,
      }),
    ).toBeNull();
    expect(
      audit.unexpectedFailure(completedRequest, {
        errorText: "net::ERR_ABORTED",
        method: "DELETE",
        url,
      }),
    ).toBe("DELETE document-detail net::ERR_ABORTED");
  });

  it("does not treat a non-204 DELETE response as a completed cancellation", () => {
    const request = {};
    const url =
      "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents/doc_0123456789abcdef0123456789abcdef";
    const audit = new E2ERequestFailureAudit<object>("project-e2e");
    audit.observeResponse(request, { method: "DELETE", status: 500, url });

    expect(
      audit.unexpectedFailure(request, {
        errorText: "net::ERR_ABORTED",
        method: "DELETE",
        url,
      }),
    ).toBe("DELETE document-detail net::ERR_ABORTED");
  });

  it.each([
    {
      errorText: "net::ERR_ABORTED",
      method: "GET",
      url: "http://127.0.0.1:15173/api/v1/projects/project-other/knowledge/documents?limit=50",
      expected: "GET outside-allowlist net::ERR_ABORTED",
    },
    {
      errorText: "net::ERR_ABORTED",
      method: "GET",
      url: "http://127.0.0.1:15173/v1/knowledge/documents?limit=50",
      expected: "GET outside-allowlist net::ERR_ABORTED",
    },
    {
      errorText: "net::ERR_ABORTED",
      method: "GET",
      url: "https://attacker.invalid/api/v1/projects/project-e2e/knowledge/documents",
      expected: "GET outside-allowlist net::ERR_ABORTED",
    },
    {
      errorText: "net::ERR_FAILED",
      method: "GET",
      url: "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents?limit=50",
      expected: "GET document-list net::ERR_FAILED",
    },
    {
      errorText: "net::ERR_ABORTED",
      method: "POST",
      url: "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents",
      expected: "POST document-list net::ERR_ABORTED",
    },
    {
      errorText: "net::ERR_ABORTED",
      method: "GET",
      url: "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/answers",
      expected: "GET outside-allowlist net::ERR_ABORTED",
    },
    {
      errorText: "provider-secret failure detail",
      method: "GET",
      url: "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents?secret=value",
      expected: "GET document-list unrecognized-browser-error",
    },
    {
      errorText: "net::ERR_ABORTED",
      method: "GET",
      url: "http://127.0.0.1:18000/api/v1/projects/project-e2e/knowledge/documents?limit=50",
      expected: "GET outside-allowlist net::ERR_ABORTED",
    },
    {
      errorText: "net::ERR_ABORTED",
      method: "GET",
      url: "http://provider-secret@127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents?limit=50",
      expected: "GET outside-allowlist net::ERR_ABORTED",
    },
    {
      errorText: "net::ERR_ABORTED",
      method: "GET",
      url: "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents?limit=50#provider-secret",
      expected: "GET outside-allowlist net::ERR_ABORTED",
    },
    {
      errorText: "net::ERR_ABORTED",
      method: "GET",
      url: "http://127.0.0.1:15173/api/v1/projects/project-e2e/knowledge/documents/doc_provider-secret",
      expected: "GET outside-allowlist net::ERR_ABORTED",
    },
  ])("retains an unexpected failure as closed diagnostics", (testCase) => {
    const result = new E2ERequestFailureAudit<object>(
      "project-e2e",
    ).unexpectedFailure({}, testCase);

    expect(result).toBe(testCase.expected);
    expect(result).not.toContain("provider-secret");
    expect(result).not.toContain("attacker.invalid");
    expect(result).not.toContain("?secret");
    expect(result).not.toContain("doc_");
  });
});
