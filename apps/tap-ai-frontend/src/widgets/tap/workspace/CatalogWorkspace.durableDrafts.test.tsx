import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { CatalogWorkspace } from "./CatalogWorkspace";
import { WORKSPACE_COPY } from "./copy";

const skill = {
  assetId: "skill-1",
  revisionId: "skill-r1",
  displayName: "claims-reviewer",
  description: "Review claims",
  instructionTemplate: "Check evidence.",
  applicableTasks: ["knowledge.answer"],
  contentDigest: "digest",
  origin: "editor",
  status: "enabled",
  requestedTools: [],
};
const agent = {
  assetId: "agent-1",
  revisionId: "agent-r1",
  displayName: "Claims agent",
  description: "Review claims",
  systemInstruction: "Check evidence.",
  toolAllowlist: ["knowledge.search"],
  skillRevisionIds: ["skill-r1"],
  modelAlias: "chat",
  contentDigest: "digest",
  outputSchemaDigest: "schema",
  origin: "editor",
  status: "enabled",
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function server() {
  let skills: unknown[] = [];
  let agents: unknown[] = [];
  const requests: { url: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const body =
      typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
    requests.push({ url, body });
    if (url.endsWith("/models"))
      return Response.json({
        defaultAlias: "chat",
        items: [
          {
            alias: "chat",
            displayName: "Chat model",
            capabilities: ["chat", "structured"],
          },
        ],
      });
    if (url.endsWith("/mcp-servers")) return Response.json({ items: [] });
    if (url.endsWith("/tools"))
      return Response.json({
        items: [
          {
            toolId: "knowledge.search",
            displayName: "Knowledge search",
            description: "Search sources",
            origin: "builtin",
            effect: "read",
            enabled: true,
            callable: true,
          },
        ],
      });
    if (url.endsWith("/skills/revisions"))
      return Response.json({ items: skills });
    if (url.endsWith("/agents/revisions"))
      return Response.json({ items: agents });
    if (url.endsWith("/skills") && init?.method === "POST") {
      skills = [skill];
      return Response.json(skill);
    }
    if (url.endsWith("/agents") && init?.method === "POST") {
      agents = [agent];
      return Response.json(agent);
    }
    if (url.endsWith("/imports")) {
      skills = [skill];
      return Response.json({
        revision: skill,
        warnings: ["requested-tools-not-granted"],
      });
    }
    if (url.endsWith("/skill-r1/content"))
      return new Response(
        "---\nname: claims-reviewer\ndescription: Review claims\n---\nCheck evidence.",
      );
    if (url.endsWith("/skill-r1/status")) {
      skills = [{ ...skill, status: "disabled" }];
      return Response.json(skills[0]);
    }
    return Response.json({ code: "unexpected-request" }, { status: 404 });
  });
  return requests;
}
function workspace(kind: "skill" | "agent") {
  return render(
    <CatalogWorkspace
      kind={kind}
      copy={WORKSPACE_COPY.en}
      items={[]}
      projectId="project-a"
      onUse={vi.fn()}
    />,
  );
}

it("creates a backend Skill and reloads its immutable revision without local storage", async () => {
  const requests = server();
  const storage = vi.spyOn(Storage.prototype, "setItem");
  workspace("skill");
  await userEvent.click(screen.getByRole("button", { name: "Create skill" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "Name (kebab-case)" }),
    "claims-reviewer",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Description" }),
    "Review claims",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Instructions" }),
    "Check evidence.",
  );
  await userEvent.click(screen.getByRole("button", { name: "Save skill" }));
  const row = await screen.findByRole("listitem", { name: "claims-reviewer" });
  expect(row).toHaveTextContent("Review claims");
  expect(requests.find((r) => r.url.endsWith("/skills"))?.body).toEqual({
    name: "claims-reviewer",
    description: "Review claims",
    instructions: "Check evidence.",
  });
  expect(storage).not.toHaveBeenCalled();
  await userEvent.click(
    within(row).getByRole("button", { name: "Revision history" }),
  );
  expect(
    await within(
      screen.getByRole("dialog", { name: "Revision history" }),
    ).findByText(/Check evidence\./),
  ).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await userEvent.click(within(row).getByRole("button", { name: "Disable" }));
  expect(await screen.findByRole("button", { name: "Enable" })).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Use claims-reviewer in chat" }),
  ).toBeDisabled();
});

it("previews an inert SKILL.md and imports only after confirmation", async () => {
  const requests = server();
  workspace("skill");
  const file = new File(
    [
      "---\nname: claims-reviewer\ndescription: Review claims\nallowed-tools: [knowledge.search]\n---\nCheck evidence.",
    ],
    "SKILL.md",
    { type: "text/markdown" },
  );
  fireEvent.change(screen.getByLabelText("Import SKILL.md"), {
    target: { files: [file] },
  });
  const dialog = await screen.findByRole("dialog", { name: "Import SKILL.md" });
  expect(dialog).toHaveTextContent("claims-reviewer");
  expect(dialog).toHaveTextContent("knowledge.search");
  expect(dialog).toHaveTextContent(/not granted/i);
  expect(requests.some((r) => r.url.endsWith("/imports"))).toBe(false);
  await userEvent.click(
    within(dialog).getByRole("button", { name: "Confirm import" }),
  );
  expect(
    await screen.findByRole("listitem", { name: "claims-reviewer" }),
  ).toBeVisible();
  expect(requests.find((r) => r.url.endsWith("/imports"))?.body).toBeInstanceOf(
    FormData,
  );
});

it("saves an Agent with selected Skills, tools and model", async () => {
  const requests = server();
  workspace("agent");
  await userEvent.click(screen.getByRole("button", { name: "Create agent" }));
  await userEvent.type(
    screen.getByRole("textbox", { name: "Name" }),
    "Claims agent",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Description" }),
    "Review claims",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Instructions" }),
    "Check evidence.",
  );
  await userEvent.click(
    await screen.findByRole("checkbox", { name: /Knowledge search/ }),
  );
  await userEvent.selectOptions(screen.getByLabelText("Model"), "chat");
  await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
  expect(
    await screen.findByRole("listitem", { name: "Claims agent" }),
  ).toBeVisible();
  expect(requests.find((r) => r.url.endsWith("/agents"))?.body).toEqual({
    displayName: "Claims agent",
    description: "Review claims",
    systemInstruction: "Check evidence.",
    skillRevisionIds: [],
    toolAllowlist: ["knowledge.search"],
    modelAlias: "chat",
  });
});

it("shows readable MCP registration errors and allows retry", async () => {
  server();
  const baseFetch = fetch;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) =>
    url.endsWith("/mcp-servers") && init?.method === "POST"
      ? Promise.resolve(
          Response.json({ code: "mcp-url-not-allowed" }, { status: 400 }),
        )
      : baseFetch(url, init),
  );
  workspace("agent");
  await userEvent.click(screen.getByRole("tab", { name: "Tools" }));
  await userEvent.click(
    screen.getByRole("button", { name: "Register MCP server" }),
  );
  for (const [label, value] of [
    ["Slug", "claims"],
    ["Server name", "Claims tools"],
    ["URL", "https://example.com/mcp"],
    ["Authentication environment variable", "CLAIMS_TOKEN"],
  ])
    await userEvent.type(screen.getByLabelText(label), value);
  await userEvent.click(
    screen.getByRole("button", { name: "Register and sync" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "This MCP URL is not allowed",
  );
  expect(
    screen.getByRole("button", { name: "Register and sync" }),
  ).toBeEnabled();
});

it("reloads a failed management list without displaying another project's revisions", async () => {
  server();
  const baseFetch = fetch;
  let failed = true;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) =>
    failed && url.includes("project-a")
      ? Promise.resolve(
          Response.json({ code: "catalog-unavailable" }, { status: 503 }),
        )
      : baseFetch(url, init),
  );
  const view = workspace("skill");
  expect(await screen.findByRole("alert")).toHaveTextContent("Retry");
  failed = false;
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("No matching items")).toBeVisible();
  view.rerender(
    <CatalogWorkspace
      kind="skill"
      copy={WORKSPACE_COPY.en}
      items={[]}
      projectId="project-b"
      onUse={vi.fn()}
    />,
  );
  expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
});

it("discards loaded revisions immediately when the project changes", async () => {
  server();
  const baseFetch = fetch;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) =>
    url.includes("project-b")
      ? new Promise<Response>(() => {})
      : url.endsWith("/skills/revisions")
        ? Promise.resolve(Response.json({ items: [skill] }))
        : baseFetch(url, init),
  );
  const view = workspace("skill");
  expect(
    await screen.findByRole("listitem", { name: "claims-reviewer" }),
  ).toBeVisible();
  view.rerender(
    <CatalogWorkspace
      kind="skill"
      copy={WORKSPACE_COPY.en}
      items={[]}
      projectId="project-b"
      onUse={vi.fn()}
    />,
  );
  expect(
    screen.queryByRole("listitem", { name: "claims-reviewer" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Loading catalog");
});

it("downloads the exact server Skill bytes including its UTF-8 marker", async () => {
  server();
  const baseFetch = fetch;
  const bytes = new Uint8Array([
    239, 187, 191, 35, 32, 83, 107, 105, 108, 108, 13, 10,
  ]);
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) =>
    url.endsWith("/skills/revisions")
      ? Promise.resolve(Response.json({ items: [skill] }))
      : url.endsWith("/skill-r1/content")
        ? Promise.resolve(new Response(bytes))
        : baseFetch(url, init),
  );
  let downloaded: Blob | undefined;
  vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
    downloaded = blob as Blob;
    return "blob:skill";
  });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  workspace("skill");
  await userEvent.click(
    await screen.findByRole("button", { name: "Download Markdown" }),
  );
  expect(new Uint8Array(await downloaded!.arrayBuffer())).toEqual(bytes);
});

it("lets an Agent remove disabled Skills and disabled or removed tools", async () => {
  server();
  const baseFetch = fetch;
  let revised: unknown;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url.endsWith("/agents/revisions"))
      return Promise.resolve(
        Response.json({
          items: [
            {
              ...agent,
              toolAllowlist: ["mcp.claims.search", "mcp.claims.removed"],
              skillRevisionIds: ["skill-r1", "missing-skill"],
            },
          ],
        }),
      );
    if (url.endsWith("/skills/revisions"))
      return Promise.resolve(
        Response.json({ items: [{ ...skill, status: "disabled" }] }),
      );
    if (url.endsWith("/tools"))
      return Promise.resolve(
        Response.json({
          items: [
            {
              toolId: "mcp.claims.search",
              displayName: "Claims search",
              origin: "mcp",
              effect: "read",
              enabled: false,
              callable: true,
            },
          ],
        }),
      );
    if (url.endsWith("/agents/agent-1/revisions") && init?.method === "POST") {
      revised = JSON.parse(String(init.body));
      return Promise.resolve(
        Response.json({ ...agent, revisionId: "agent-r2" }),
      );
    }
    return baseFetch(url, init);
  });
  workspace("agent");
  await userEvent.click(
    await screen.findByRole("button", { name: "Edit Claims agent" }),
  );
  const disabledSkill = screen.getByRole("checkbox", {
    name: /claims-reviewer/,
  });
  expect(disabledSkill).toBeChecked();
  await userEvent.click(disabledSkill);
  await userEvent.click(
    screen.getByRole("checkbox", { name: /missing-skill/ }),
  );
  await userEvent.click(
    screen.getByRole("checkbox", { name: /Claims search/ }),
  );
  await userEvent.click(
    screen.getByRole("checkbox", { name: /mcp.claims.removed/ }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Save agent" }));
  expect(revised).toMatchObject({ skillRevisionIds: [], toolAllowlist: [] });
});

it("can re-enable an older disabled revision from history", async () => {
  server();
  const baseFetch = fetch;
  let enabled = false;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url.endsWith("/skills/revisions"))
      return Promise.resolve(
        Response.json({
          items: [
            {
              ...skill,
              revisionId: "skill-r2",
              adoptedFromRevisionId: skill.revisionId,
            },
            { ...skill, status: enabled ? "enabled" : "disabled" },
          ],
        }),
      );
    if (url.endsWith("/skill-r1/status") && init?.method === "POST") {
      enabled = true;
      return Promise.resolve(Response.json(skill));
    }
    return baseFetch(url, init);
  });
  workspace("skill");
  await userEvent.click(
    await screen.findByRole("button", { name: "Revision history" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Enable skill-r1" }),
  );
  expect(enabled).toBe(true);
});
