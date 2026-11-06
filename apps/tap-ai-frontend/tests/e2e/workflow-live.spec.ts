import { expect, test, type Request } from "@playwright/test";
import { preparePublishedFixture } from "./publicationFixture";

async function publishWorkflowSource(
  page: import("@playwright/test").Page,
  root: string,
  origin: string,
): Promise<string> {
  const filename = `workflow-evidence-${crypto.randomUUID()}.md`;
  const upload = await page.request.post(`${root}/knowledge/sources`, {
    headers: { Origin: origin, "Idempotency-Key": crypto.randomUUID() },
    multipart: {
      upload: {
        name: filename,
        mimeType: "text/markdown",
        buffer: Buffer.from(
          "# Review guide\n\nCheck deterministic edge cases.",
        ),
      },
    },
  });
  expect(upload.status(), await upload.text()).toBe(202);
  const { source } = (await upload.json()) as { source: { sourceId: string } };
  let revisionId: string | null = null;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/knowledge/sources/${source.sourceId}?limit=50`,
        );
        expect(response.ok()).toBe(true);
        const detail = (await response.json()) as {
          documents: { items: { revisionId: string; status: string }[] };
        };
        revisionId =
          detail.documents.items.find((item) => item.status === "ready")
            ?.revisionId ?? null;
        return revisionId;
      },
      { timeout: 45_000 },
    )
    .not.toBeNull();
  preparePublishedFixture([revisionId!]);
  return filename;
}

async function selectAgent(
  page: import("@playwright/test").Page,
  name: RegExp,
) {
  await page.getByRole("button", { name: "Add to message" }).click();
  await page
    .getByRole("menu", { name: "Add to message" })
    .getByRole("menuitem", { name: "Use Agents" })
    .click();
  await page
    .getByRole("dialog", { name: "Use Agents" })
    .getByRole("option", { name })
    .click();
  await expect(page.getByRole("dialog", { name: "Use Agents" })).toHaveCount(0);
  await expect(
    page.getByRole("form", { name: "Message composer" }).getByRole("button", {
      name: new RegExp(`Remove .*${name.source}`, "u"),
    }),
  ).toBeVisible();
}

/** Runs only under scripts/run-workflow-e2e.py against the real isolated stack. */
test("real API, Relay, native worker and Web show one deterministic Agent workflow", async ({
  page,
}) => {
  expect(process.env.TAPPER_WORKFLOW_E2E).toBe("1");
  const origin = process.env.TAPPER_WEB_BASE_URL;
  expect(origin).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/u);
  const runtime = await page.request.get("/api/v1/runtime-mode");
  expect(runtime.ok()).toBe(true);
  const mode = (await runtime.json()) as {
    projectId: string;
    workflowEnabled: boolean;
  };
  expect(mode.workflowEnabled).toBe(true);
  const root = `/api/v1/projects/${encodeURIComponent(mode.projectId)}`;
  const goal = "Review the deterministic case [e2e-workflow:single:qae]";
  const created = await page.request.post(`${root}/workflows`, {
    headers: { Origin: origin!, "Idempotency-Key": crypto.randomUUID() },
    data: {
      goal,
      modelAlias: "qwen-plus",
      requestedAgentIds: ["qae"],
      sourceRevisionIds: [],
      documentRevisionIds: [],
    },
  });
  expect(created.status(), await created.text()).toBe(202);
  const initial = (await created.json()) as { taskId: string; origin: string };
  expect(initial.origin).toBe("native");
  const taskPath = `${root}/workflows/${initial.taskId}`;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(taskPath);
        expect(response.ok()).toBe(true);
        const body = (await response.json()) as { status: string };
        if (body.status === "failed")
          throw new Error(
            `Native task failed: ${JSON.stringify(body).slice(-6000)}`,
          );
        return body.status;
      },
      { timeout: 30_000, intervals: [500, 1000, 2000] },
    )
    .toBe("succeeded");
  const snapshot = (await (await page.request.get(taskPath)).json()) as {
    taskId: string;
    origin: string;
    nodes: { agentId: string; parentNodeId: string | null; status: string }[];
    events: { kind: string }[];
  };
  expect(snapshot.origin).toBe("native");
  expect(snapshot.nodes).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ agentId: "qae", status: "succeeded" }),
    ]),
  );
  expect(snapshot.events.map((event) => event.kind)).toContain(
    "task.completed",
  );

  await page.goto("/");
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByRole("tab", { name: "Runs", exact: true }).click();
  const run = page
    .getByRole("region", { name: "Workflow runs" })
    .getByRole("button")
    .filter({ hasText: initial.taskId });
  await expect(run).toBeVisible();
  await run.click();
  await expect(
    page.getByRole("heading", { name: "Task progress" }),
  ).toBeVisible();
  await expect(page.getByText(`Run ${initial.taskId}`)).toBeVisible();
  await expect(page.getByRole("button", { name: /QAE/u })).toBeVisible();
});

test("real Conversation UI links a selected Agent run and restores its saved progress", async ({
  page,
}) => {
  expect(process.env.TAPPER_WORKFLOW_E2E).toBe("1");
  await page.goto("/");
  await page.getByRole("button", { name: "New chat" }).click();
  await selectAgent(page, /QAE/u);
  await page
    .getByRole("textbox", { name: "Message Tapper" })
    .fill("Review the deterministic case [e2e-workflow:single:qae]");
  const acceptedResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname.endsWith("/conversations"),
  );
  await page.getByRole("button", { name: "Send" }).click();
  const accepted = await acceptedResponse;
  expect(accepted.status(), await accepted.text()).toBe(202);
  const { conversationId } = (await accepted.json()) as {
    conversationId: string;
  };
  const runtime = (await (
    await page.request.get("/api/v1/runtime-mode")
  ).json()) as {
    projectId: string;
  };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  let taskId: string | null = null;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/conversations/${conversationId}`,
        );
        expect(response.ok()).toBe(true);
        const detail = (await response.json()) as {
          turns: { workflowTaskId: string | null }[];
        };
        taskId = detail.turns.at(-1)?.workflowTaskId ?? null;
        return taskId;
      },
      { timeout: 45_000 },
    )
    .not.toBeNull();
  await expect(
    page.getByRole("button", { name: "View execution details" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "View execution details" }).click();
  await expect(
    page.getByRole("heading", { name: "Task progress" }),
  ).toBeVisible();
  await expect(page.getByText(`Run ${taskId}`)).toBeVisible();
  await expect(page.locator(".tap-workflow-answer")).toHaveText(
    "Merged deterministic workflow answer",
    { timeout: 60_000 },
  );
  await page.screenshot({ path: "test-results/workflow-live-desktop.png" });
  await page.getByRole("button", { name: "Close workflow details" }).click();
  await expect(
    page.getByRole("complementary", { name: "Workflow details" }),
  ).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "View execution details" }),
  ).toBeVisible();
  await expect(page.locator(".tap-workflow-answer")).toHaveText(
    "Merged deterministic workflow answer",
  );
  await page.getByRole("button", { name: "View execution details" }).click();
  await expect(page.getByText(`Run ${taskId}`)).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: "Task progress" }),
  ).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page
    .getByRole("complementary", { name: "Workflow details" })
    .getByRole("button", { name: /^QAE/u })
    .click();
  await expect(
    page.getByRole("heading", { name: "Node details" }),
  ).toBeFocused();
  await expect(page.getByText("Loading log…")).toHaveCount(0);
  await page.screenshot({ path: "test-results/workflow-live-mobile.png" });
  await page.keyboard.press("Escape");
  await expect(
    page.getByRole("complementary", { name: "Workflow details" }),
  ).toHaveCount(0);
  await expect(page.locator(".tap-panel-toggle--right-expand")).toBeFocused();
});

test("two selected specialists expose an in-flight guidance receipt and public tool steps", async ({
  page,
}) => {
  expect(process.env.TAPPER_WORKFLOW_E2E).toBe("1");
  const eventRequests: Request[] = [];
  const failedEventRequests: Request[] = [];
  const isWorkflowStream = (request: Request) =>
    new URL(request.url()).pathname.includes("/workflows/") &&
    new URL(request.url()).pathname.endsWith("/events");
  page.on("request", (request) => {
    if (isWorkflowStream(request)) eventRequests.push(request);
  });
  page.on("requestfailed", (request) => {
    if (isWorkflowStream(request)) failedEventRequests.push(request);
  });
  const origin = process.env.TAPPER_WEB_BASE_URL!;
  const runtime = (await (
    await page.request.get("/api/v1/runtime-mode")
  ).json()) as { projectId: string };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  const sourceName = await publishWorkflowSource(page, root, origin);
  await page.goto("/");
  await page.getByRole("button", { name: "New chat" }).click();
  await page
    .getByRole("checkbox", { name: new RegExp(sourceName, "u") })
    .check();
  await selectAgent(page, /QAE/u);
  await selectAgent(page, /SDET/u);
  await page
    .getByRole("textbox", { name: "Message Tapper" })
    .fill("Review the deterministic case [e2e-workflow:double:qae:sdet]");
  const acceptedResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname.endsWith("/conversations"),
  );
  await page.getByRole("button", { name: "Send" }).click();
  const accepted = await acceptedResponse;
  expect(accepted.status(), await accepted.text()).toBe(202);
  const { conversationId } = (await accepted.json()) as {
    conversationId: string;
  };
  let taskId: string | null = null;
  await expect
    .poll(
      async () => {
        const detail = (await (
          await page.request.get(`${root}/conversations/${conversationId}`)
        ).json()) as {
          turns: { workflowTaskId: string | null }[];
        };
        taskId = detail.turns.at(-1)?.workflowTaskId ?? null;
        return taskId;
      },
      { timeout: 45_000, intervals: [250, 500, 1000] },
    )
    .not.toBeNull();
  await page.getByRole("button", { name: "View execution details" }).click();
  await expect(
    page.getByRole("heading", { name: "Task progress" }),
  ).toBeVisible();
  const taskPath = `${root}/workflows/${taskId}`;
  await expect
    .poll(
      async () => {
        const snapshot = (await (await page.request.get(taskPath)).json()) as {
          nodes: { agentId: string; status: string }[];
        };
        return snapshot.nodes.find((node) => node.agentId === "qae")?.status;
      },
      { timeout: 25_000, intervals: [250, 500] },
    )
    .toBe("running");
  await page
    .getByRole("complementary", { name: "Workflow details" })
    .getByRole("button", { name: /^QAE/u })
    .click();
  await expect(
    page.getByRole("heading", { name: "Node details" }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "Additional requirement" })
    .fill("Check edge cases");
  await page.getByRole("button", { name: "Send to this node" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Request accepted" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close workflow details" }).click();
  await expect(
    page.getByRole("complementary", { name: "Workflow details" }),
  ).toHaveCount(0);
  const ownStreams = () =>
    eventRequests.filter((request) =>
      new URL(request.url()).pathname.endsWith(`/workflows/${taskId}/events`),
    );
  await expect.poll(() => ownStreams().length).toBeGreaterThan(0);
  const beforeDisconnect = ownStreams().length;
  await page.context().setOffline(true);
  expect(await page.evaluate(() => navigator.onLine)).toBe(false);
  await expect
    .poll(
      () =>
        failedEventRequests.filter((request) =>
          new URL(request.url()).pathname.endsWith(
            `/workflows/${taskId}/events`,
          ),
        ).length,
      { timeout: 10_000 },
    )
    .toBeGreaterThan(0);
  await page.context().setOffline(false);
  expect(await page.evaluate(() => navigator.onLine)).toBe(true);
  await expect
    .poll(() => ownStreams().length, { timeout: 15_000 })
    .toBeGreaterThan(beforeDisconnect);
  const resumedCursor = Number(ownStreams().at(-1)!.headers()["last-event-id"]);
  expect(resumedCursor).toBeGreaterThan(0);
  await expect
    .poll(
      async () => {
        const snapshot = (await (await page.request.get(taskPath)).json()) as {
          status: string;
          nodes: { agentId: string; status: string; publicSummary?: string }[];
          commands: { kind: string; status: string }[];
        };
        if (snapshot.status === "failed")
          throw new Error("Double-Agent task failed");
        return snapshot;
      },
      { timeout: 60_000, intervals: [500, 1000] },
    )
    .toMatchObject({ status: "succeeded" });
  const final = (await (await page.request.get(taskPath)).json()) as {
    nodes: { agentId: string; status: string; publicSummary?: string }[];
    commands: { kind: string; status: string }[];
    events: { kind: string; payload: Record<string, unknown> }[];
  };
  expect(
    final.nodes.filter((node) => ["qae", "sdet"].includes(node.agentId)),
  ).toHaveLength(2);
  expect(final.commands).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ kind: "steer", status: "applied" }),
    ]),
  );
  expect(
    final.nodes.find((node) => node.agentId === "qae")?.publicSummary,
  ).toContain("Check edge cases");
  expect(final.events).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        kind: "node.tool_finished",
        payload: expect.objectContaining({
          tool_id: "knowledge.search",
          outcome: "succeeded",
        }),
      }),
    ]),
  );
  await expect(page.locator(".tap-workflow-answer")).toHaveText(
    "Merged deterministic workflow answer",
  );
  await page.getByRole("button", { name: "View execution details" }).click();
  const backToWorkflow = page.getByRole("button", { name: "Back to workflow" });
  await expect(
    page.getByRole("heading", { name: "Node details" }),
  ).toBeVisible();
  await backToWorkflow.click();
  await expect(
    page.getByRole("heading", { name: "Task progress" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "View execution details" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "View execution details" }).click();
  if (await backToWorkflow.isVisible()) await backToWorkflow.click();
  await expect(
    page.getByRole("heading", { name: "Task progress" }),
  ).toBeVisible();
  await page
    .getByRole("complementary", { name: "Workflow details" })
    .getByRole("button", { name: /^QAE/u })
    .click();
  await page.getByRole("button", { name: "Back to workflow" }).click();
  await expect(
    page.getByText("Tool succeeded: knowledge.search").first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close workflow details" }).click();
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.getByRole("tab", { name: "Team", exact: true }).click();
  await page
    .getByRole("cell", { name: /^QAE ·/u })
    .getByRole("button", { name: /^QAE ·/u })
    .click();
  const profile = page.getByRole("dialog", { name: /QAE/u });
  await profile.getByRole("tab", { name: "Runs" }).click();
  await expect
    .poll(() =>
      profile
        .getByRole("region", { name: "Workflow runs" })
        .getByRole("listitem")
        .count(),
    )
    .toBeGreaterThan(1);
  await expect(profile.getByText(`Run ${taskId}`)).toBeVisible();
});
