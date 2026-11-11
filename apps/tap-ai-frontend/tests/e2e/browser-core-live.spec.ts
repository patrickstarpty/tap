import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

type CoreCase = {
  name: string;
  goal: string;
  startUrl: string;
  inputNotes?: string;
  expected: string;
  status: "succeeded" | "failed" | "awaiting_input" | "canceled";
  outputPattern?: string;
  replay?: boolean;
  acceptanceContract?: {
    version: 1;
    reviewed: true;
    fields: {
      id: string;
      value: string;
      readbackValue: string;
      attribute: string;
      targetTexts: string[];
      scopeText?: string | null;
    }[];
  };
};

type CoreRun = {
  status: string;
  outputs: unknown[];
  steps: { status: string }[];
  evidence: { kind: string; url: string; sha256: string; evidenceId: string }[];
  bundleId: string;
  inputSha256: string;
  executionId: string;
  replayModelCalls: number;
};

type CoreSnapshot = {
  status: string;
  taskKind: string;
  taskId: string;
  browserRun: CoreRun | null;
  browserInputRequest?: { question: string } | null;
  replayOfTaskId?: string | null;
  budgetUsed?: { model_calls?: number };
  events?: {
    kind: string;
    sequence: number;
    payload: Record<string, unknown>;
  }[];
};

const active = process.env.TAPPER_BROWSER_CORE_E2E === "1";
const cases: CoreCase[] = active
  ? JSON.parse(readFileSync(process.env.TAPPER_BROWSER_CORE_CASES!, "utf8"))
  : [];
const taskTimeoutMs = Number(
  process.env.TAPPER_BROWSER_CORE_TASK_TIMEOUT_MS ?? 240_000,
);

test.skip(!active, "requires isolated browser core supervisor");
test.describe.configure({
  mode: "serial",
  timeout: taskTimeoutMs * 2 + 120_000,
});

for (const scenario of cases) {
  test(`${scenario.name}: Chat to persisted browser receipt`, async ({
    page,
  }) => {
    const origin = process.env.TAPPER_WEB_BASE_URL!;
    const evidenceDir = process.env.TAPPER_BROWSER_CORE_EVIDENCE_DIR!;
    const runtimeResponse = await page.request.get("/api/v1/runtime-mode");
    expect(runtimeResponse.ok()).toBe(true);
    const runtime = (await runtimeResponse.json()) as {
      projectId: string;
      browserEnabled: boolean;
      workflowEnabled: boolean;
    };
    expect(runtime.browserEnabled).toBe(true);
    expect(runtime.workflowEnabled).toBe(true);
    const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;

    await page.goto("/");
    await page.getByRole("button", { name: "New chat" }).click();
    await expect(
      page.getByRole("checkbox", { name: "Browser run" }),
    ).toHaveCount(0);
    await page
      .getByRole("textbox", { name: "Message Tapper" })
      .fill(
        `${process.env.TAPPER_BROWSER_CORE_SYNTHETIC === "1" ? "[e2e-workflow:browser] " : ""}${scenario.goal}\nWebsite: ${scenario.startUrl}\n${scenario.inputNotes ?? ""}\nExpected: ${scenario.expected}`,
      );
    const acceptanceContract =
      scenario.acceptanceContract ??
      (process.env.TAPPER_BROWSER_CORE_LCA_BRIDGE === "1" &&
      scenario.name === "form"
        ? {
            version: 1 as const,
            reviewed: true as const,
            fields: [
              {
                id: "account",
                value: "AC-42",
                readbackValue: "AC-42",
                attribute: "value",
                targetTexts: ["Account"],
              },
              {
                id: "amount",
                value: "120",
                readbackValue: "120",
                attribute: "value",
                targetTexts: ["Amount"],
              },
            ],
          }
        : undefined);
    if (acceptanceContract) {
      await page.getByText("Execution options", { exact: true }).click();
      await page
        .getByLabel("Execution", { exact: true })
        .selectOption("browser");
      await page
        .getByLabel("Start URL", { exact: true })
        .fill(scenario.startUrl);
      for (const [offset, requirement] of acceptanceContract.fields.entries()) {
        const index = offset + 1;
        await page
          .getByRole("button", { name: "Add required input", exact: true })
          .click();
        const field = page.getByRole("group", {
          name: `Required input ${index}`,
          exact: true,
        });
        await field
          .getByLabel("Field ID", { exact: true })
          .fill(requirement.id);
        await field
          .getByLabel("Input value", { exact: true })
          .fill(requirement.value);
        await field
          .getByLabel("Expected readback value", { exact: true })
          .fill(requirement.readbackValue);
        await field
          .getByRole("combobox", { name: "Readback attribute", exact: true })
          .selectOption(requirement.attribute);
        await field
          .getByLabel("Exact business labels (one per line)", { exact: true })
          .fill(requirement.targetTexts.join("\n"));
        if ("scopeText" in requirement && requirement.scopeText) {
          await field
            .getByLabel("Scope text (optional)", { exact: true })
            .fill(requirement.scopeText);
        }
      }
      await expect(
        page.getByRole("button", { name: "Send", exact: true }),
      ).toBeDisabled();
      await page
        .getByRole("checkbox", {
          name: "I reviewed every required input and its expected readback.",
          exact: true,
        })
        .check();
    }
    const acceptedResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/conversations"),
    );
    await page.getByRole("button", { name: "Send" }).click();
    const accepted = await acceptedResponse;
    expect(accepted.status(), await accepted.text()).toBe(202);
    if (acceptanceContract) {
      const submitted = accepted.request().postDataJSON();
      expect(submitted.browser.acceptanceContract.reviewed).toBe(true);
      const normalizedFields = (
        fields: NonNullable<CoreCase["acceptanceContract"]>["fields"],
      ) =>
        fields.map((field) => ({
          id: field.id,
          value: field.value,
          readbackValue: field.readbackValue,
          attribute: field.attribute,
          targetTexts: field.targetTexts,
          scopeText: field.scopeText ?? null,
        }));
      expect(
        normalizedFields(submitted.browser.acceptanceContract.fields),
      ).toEqual(normalizedFields(acceptanceContract.fields));
      writeFileSync(
        `${evidenceDir}/${scenario.name}-reviewed-input-request.json`,
        JSON.stringify(submitted.browser, null, 2),
      );
    }
    const { conversationId } = (await accepted.json()) as {
      conversationId: string;
    };
    let taskId = "";
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `${root}/conversations/${conversationId}`,
          );
          expect(response.ok()).toBe(true);
          const body = (await response.json()) as {
            turns: { workflowTaskId: string | null }[];
          };
          taskId = body.turns.at(-1)?.workflowTaskId ?? "";
          return taskId;
        },
        { timeout: 45_000 },
      )
      .not.toBe("");
    const taskPath = `${root}/workflows/${taskId}`;
    let snapshot = {} as CoreSnapshot;
    await expect
      .poll(
        async () => {
          const response = await page.request.get(taskPath);
          expect(response.ok()).toBe(true);
          snapshot = (await response.json()) as CoreSnapshot;
          if (
            snapshot.status === "recovering" ||
            (["failed", "canceled", "awaiting_input"].includes(
              snapshot.status,
            ) &&
              snapshot.status !== scenario.status)
          ) {
            writeFileSync(
              `${evidenceDir}/${scenario.name}-unexpected-snapshot.json`,
              JSON.stringify(snapshot, null, 2),
            );
            throw new Error(`Browser task stopped at ${snapshot.status}`);
          }
          return snapshot.status;
        },
        { timeout: taskTimeoutMs, intervals: [500, 1000, 2000] },
      )
      .toBe(scenario.status);
    expect(snapshot.taskKind).toBe("browser");
    const plan = snapshot.events?.find(
      (event) => event.kind === "browser.plan_created",
    );
    const firstAction = snapshot.events?.find(
      (event) => event.kind === "browser.action_started",
    );
    expect(plan).toBeDefined();
    expect(firstAction).toBeDefined();
    expect(plan!.sequence).toBeLessThan(firstAction!.sequence);
    expect(firstAction!.payload.step_id).toBeTruthy();
    expect(snapshot.taskId).toBe(taskId);
    writeFileSync(
      `${evidenceDir}/${scenario.name}-snapshot.json`,
      JSON.stringify(snapshot, null, 2),
    );

    if (scenario.status !== "succeeded") {
      expect(snapshot.browserRun?.status === "passed").toBe(false);
      if (scenario.status === "awaiting_input") {
        expect(snapshot.browserInputRequest?.question).toBeTruthy();
      }
      return;
    }

    const run = snapshot.browserRun!;
    expect(run.status).toBe("passed");
    expect(run.outputs.length).toBeGreaterThan(0);
    if (scenario.outputPattern) {
      expect(JSON.stringify(run.outputs)).toMatch(
        new RegExp(scenario.outputPattern, "u"),
      );
    }
    expect(run.steps.some((step) => step.status === "passed")).toBe(true);
    const evidence: Record<string, { sha256: string; evidenceId: string }> = {};
    for (const item of run.evidence) {
      const response = await page.request.get(item.url);
      expect(response.ok()).toBe(true);
      const bytes = await response.body();
      expect(createHash("sha256").update(bytes).digest("hex")).toBe(
        item.sha256,
      );
      if (item.kind === "screenshot") {
        expect(
          bytes
            .subarray(0, 8)
            .equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])),
        ).toBe(true);
        expect(bytes.readUInt32BE(16)).toBeGreaterThan(100);
        expect(bytes.readUInt32BE(20)).toBeGreaterThan(100);
      }
      if (item.kind === "trace") {
        expect(bytes.subarray(0, 2).toString()).toBe("PK");
        expect(bytes.length).toBeGreaterThan(1000);
      }
      writeFileSync(
        `${evidenceDir}/${scenario.name}-${item.kind}-${item.evidenceId}`,
        bytes,
      );
      evidence[item.kind] = {
        sha256: item.sha256,
        evidenceId: item.evidenceId,
      };
    }
    expect(evidence.screenshot).toBeDefined();
    expect(evidence.trace).toBeDefined();
    if (process.env.TAPPER_BROWSER_CORE_LCA_BRIDGE !== "1")
      expect(evidence.video).toBeDefined();
    await page.getByRole("button", { name: "View progress" }).click();
    await expect(page.getByText(/^Browser result ·/u)).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Screenshot" }),
    ).not.toBeVisible();
    await page.getByText(/^Browser result ·/u).click();
    await expect(page.getByRole("link", { name: "Screenshot" })).toBeVisible();
    await page.getByText(/^Browser result ·/u).click();
    await page.screenshot({
      path: `${evidenceDir}/${scenario.name}-ui.png`,
      fullPage: true,
    });
    await page.reload();
    await expect(
      page.getByRole("button", { name: "View progress" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "View progress" }).click();
    await expect(page.getByText(/^Browser result ·/u)).toBeVisible();

    const foreign = `/api/v1/projects/foreign-project/workflows/${taskId}`;
    expect((await page.request.get(foreign)).status()).toBeGreaterThanOrEqual(
      400,
    );
    expect(
      (
        await page.request.get(
          `${foreign}/browser-evidence/${evidence.screenshot.evidenceId}`,
        )
      ).status(),
    ).toBeGreaterThanOrEqual(400);
    if (
      process.env.TAPPER_BROWSER_CORE_LCA_BRIDGE === "1" &&
      scenario.name === "form"
    ) {
      const link = page.getByRole("link", { name: "Review as LCA draft" });
      await expect(link).toBeVisible();
      const href = await link.getAttribute("href");
      expect(href).toContain(`aa-task=${taskId}`);
      const lca = page;
      {
        await link.click();
        await expect(lca.getByLabel("AA task ID")).toHaveValue(taskId);
        await lca.getByRole("button", { name: "Preview AA task" }).click();
        await expect(
          lca.getByRole("button", { name: "Use as unsaved draft" }),
        ).toBeVisible();
        await lca.getByRole("button", { name: "Use as unsaved draft" }).click();
        await expect(lca.getByLabel("Flow title")).toHaveValue(
          `AA task ${taskId}`,
        );
        const saved = lca.waitForResponse(
          (r) =>
            r.request().method() === "POST" &&
            new URL(r.url()).pathname.endsWith("/lca/flows"),
        );
        await lca
          .getByRole("button", { name: "Save draft", exact: true })
          .click();
        const save = await saved;
        expect(save.ok(), await save.text()).toBe(true);
        const flow = await save.json();
        expect(
          flow.definition.steps.some(
            (s: { operation: string }) => s.operation === "assert_read",
          ),
        ).toBe(true);
        const accepted = lca.waitForResponse(
          (r) =>
            r.request().method() === "POST" &&
            new URL(r.url()).pathname.endsWith("/runs"),
        );
        await lca
          .getByRole("button", { name: "Debug replay", exact: true })
          .click();
        const replayResponse = await accepted;
        expect(replayResponse.ok(), await replayResponse.text()).toBe(true);
        const started = await replayResponse.json();
        let completed: {
          execution?: { status: string; result?: unknown };
          canonical_results?: {
            status: string;
            output?: { name: string; value: string };
          }[];
          canonical_results_error?: string | null;
          contract_acceptance?: {
            status: string;
            fields: { id: string; status: string }[];
          };
        } = {};
        await expect
          .poll(
            async () => {
              const response = await lca.request.get(
                new URL(
                  `/api/projects/${encodeURIComponent(runtime.projectId)}/lca/runs/${started.id}`,
                  href!,
                ).href,
              );
              expect(response.ok()).toBe(true);
              completed = await response.json();
              return completed.execution?.status;
            },
            { timeout: 90_000 },
          )
          .toBe("passed");
        expect(completed.canonical_results_error).toBeNull();
        expect(completed.canonical_results?.length).toBeGreaterThanOrEqual(8);
        expect(completed.contract_acceptance?.status).toBe("passed");
        expect(completed.contract_acceptance?.fields).toHaveLength(2);
        expect(
          completed.contract_acceptance?.fields.every(
            (field) => field.status === "passed",
          ),
        ).toBe(true);
        expect(
          completed.canonical_results?.every(
            (step) => step.status === "passed",
          ),
        ).toBe(true);
        expect(
          completed.canonical_results
            ?.filter((step) => step.output?.name === "result")
            .map((step) => step.output?.value),
        ).toEqual(["AC-42 / 120 / MATCH", "AC-42 / 120 / MATCH"]);
        expect(JSON.stringify(completed.execution?.result)).toContain(
          "AC-42 / 120 / MATCH",
        );
        const sourceAfterImport = await page.request.get(
          new URL(taskPath, origin).href,
        );
        expect((await sourceAfterImport.json()).budgetUsed?.model_calls).toBe(
          snapshot.budgetUsed?.model_calls,
        );
        writeFileSync(
          `${evidenceDir}/form-lca-bridge-run.json`,
          JSON.stringify(completed, null, 2),
        );
        await lca.getByRole("button", { name: "Refresh run receipt" }).click();
        await expect(
          lca
            .getByRole("article", { name: `Run ${started.id}` })
            .getByText("passed", { exact: true })
            .first(),
        ).toBeVisible();
        await lca.screenshot({
          path: `${evidenceDir}/form-lca-bridge-ui.png`,
          fullPage: true,
        });
      }
    }
    if (!scenario.replay) return;
    const modelCallsBeforeReplay = snapshot.budgetUsed?.model_calls;
    const replayResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith(`/${taskId}/replay`),
    );
    await page.getByRole("button", { name: "Replay frozen run" }).click();
    const acceptedReplay = await replayResponse;
    expect(acceptedReplay.status(), await acceptedReplay.text()).toBe(202);
    const replayBody = (await acceptedReplay.json()) as { taskId: string };
    expect(replayBody.taskId).not.toBe(taskId);
    let replay = {} as CoreSnapshot;
    await expect
      .poll(
        async () => {
          const response = await page.request.get(
            `${root}/workflows/${replayBody.taskId}`,
          );
          expect(response.ok()).toBe(true);
          replay = (await response.json()) as CoreSnapshot;
          return replay.status;
        },
        { timeout: taskTimeoutMs, intervals: [500, 1000, 2000] },
      )
      .toBe("succeeded");
    expect(replay.replayOfTaskId).toBe(taskId);
    expect(replay.browserRun!.bundleId).toBe(run.bundleId);
    expect(replay.browserRun!.inputSha256).toBe(run.inputSha256);
    expect(replay.browserRun!.executionId).not.toBe(run.executionId);
    expect(replay.browserRun!.replayModelCalls).toBe(0);
    expect(replay.budgetUsed?.model_calls ?? 0).toBe(0);
    const replayVideo = replay.browserRun!.evidence.find(
      (item) => item.kind === "video",
    );
    expect(replayVideo).toBeDefined();
    expect(replayVideo!.evidenceId).not.toBe(evidence.video.evidenceId);
    const videoResponse = await page.request.get(replayVideo!.url);
    expect(videoResponse.ok()).toBe(true);
    writeFileSync(
      `${evidenceDir}/${scenario.name}-replay.webm`,
      await videoResponse.body(),
    );

    const originalAgain = (await (
      await page.request.get(taskPath)
    ).json()) as CoreSnapshot;
    expect(originalAgain.budgetUsed?.model_calls).toBe(modelCallsBeforeReplay);
    expect(originalAgain.browserRun!.executionId).toBe(run.executionId);
    expect(originalAgain.browserRun!.evidence).toEqual(run.evidence);
    writeFileSync(
      `${evidenceDir}/${scenario.name}-replay.json`,
      JSON.stringify(replay, null, 2),
    );
    expect(
      (
        await page.request.post(`${foreign}/replay`, {
          headers: { Origin: origin },
        })
      ).status(),
    ).toBeGreaterThanOrEqual(400);
  });
}

test("cancel active browser coding through the run detail", async ({
  page,
}) => {
  test.skip(
    process.env.TAPPER_BROWSER_CORE_RUN_CANCEL !== "1",
    "full synthetic matrix only",
  );
  const evidenceDir = process.env.TAPPER_BROWSER_CORE_EVIDENCE_DIR!;
  const runtime = (await (
    await page.request.get("/api/v1/runtime-mode")
  ).json()) as {
    projectId: string;
  };
  const root = `/api/v1/projects/${encodeURIComponent(runtime.projectId)}`;
  await page.goto("/");
  await page.getByRole("button", { name: "New chat" }).click();
  await page
    .getByRole("textbox", { name: "Message Tapper" })
    .fill(
      "[e2e-workflow:browser] Core cancel: inspect http://example.test/ then continue a long verification. customer=Ada; region=west. Cancel before the final result.",
    );
  const acceptedResponse = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname.endsWith("/conversations"),
  );
  await page.getByRole("button", { name: "Send" }).click();
  const accepted = await acceptedResponse;
  expect(accepted.status()).toBe(202);
  const { conversationId } = (await accepted.json()) as {
    conversationId: string;
  };
  let taskId = "";
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${root}/conversations/${conversationId}`,
        );
        expect(response.ok()).toBe(true);
        const body = (await response.json()) as {
          turns: { workflowTaskId: string | null }[];
        };
        taskId = body.turns.at(-1)?.workflowTaskId ?? "";
        return taskId;
      },
      { timeout: 45_000 },
    )
    .not.toBe("");
  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${root}/workflows/${taskId}`);
        expect(response.ok()).toBe(true);
        const body = (await response.json()) as CoreSnapshot;
        return (
          body.events?.filter(
            (event) =>
              event.kind === "browser.action_finished" &&
              event.payload.observation_sequence,
          ).length ?? 0
        );
      },
      { timeout: 90_000, intervals: [500, 1000, 2000] },
    )
    .toBeGreaterThanOrEqual(1);
  await page.getByRole("button", { name: "View progress" }).click();
  await page.getByRole("button", { name: "Request cancel" }).click();
  let snapshot = {} as CoreSnapshot;
  await expect
    .poll(
      async () => {
        const response = await page.request.get(`${root}/workflows/${taskId}`);
        expect(response.ok()).toBe(true);
        snapshot = (await response.json()) as CoreSnapshot;
        if (snapshot.status === "recovering" || snapshot.status === "failed") {
          writeFileSync(
            `${evidenceDir}/cancel-unexpected-snapshot.json`,
            JSON.stringify(snapshot, null, 2),
          );
          throw new Error(`Cancel did not settle: ${snapshot.status}`);
        }
        return snapshot.status;
      },
      { timeout: taskTimeoutMs, intervals: [500, 1000, 2000] },
    )
    .toBe("canceled");
  writeFileSync(
    `${evidenceDir}/cancel-snapshot.json`,
    JSON.stringify(snapshot, null, 2),
  );
  await page.reload();
  await expect(
    page.getByRole("button", { name: "View progress" }),
  ).toBeVisible();
});
