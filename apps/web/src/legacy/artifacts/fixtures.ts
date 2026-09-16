import type {
  ArtifactState,
  Automation,
  BddFeature,
  BddKeyword,
  ExecutionAgent,
  ImplementationActionType,
  MobileDevice,
  TestPlan,
  TestPlanScenario,
} from "./model";

function planStep(id: string, keyword: BddKeyword, text: string) {
  return { id, keyword, text } as const;
}

const lifePlanScenarios: readonly TestPlanScenario[] = [
  {
    id: "TP-101-SC-01",
    title: "Complete application enters underwriting",
    steps: [
      planStep(
        "TP-101-ST-01",
        "Given",
        "an adult applicant starts a term life application",
      ),
      planStep(
        "TP-101-ST-02",
        "When",
        "the applicant enters policy and health details",
      ),
      planStep(
        "TP-101-ST-03",
        "And",
        "the applicant submits the completed application",
      ),
      planStep(
        "TP-101-ST-04",
        "Then",
        "the application enters pending underwriting",
      ),
    ],
  },
  {
    id: "TP-101-SC-02",
    title: "Missing health disclosure is blocked",
    steps: [
      planStep(
        "TP-101-ST-05",
        "Given",
        "mandatory health disclosure answers are missing",
      ),
      planStep("TP-101-ST-06", "When", "the applicant submits the application"),
      planStep("TP-101-ST-07", "Then", "the missing disclosures are shown"),
    ],
  },
  {
    id: "TP-101-SC-03",
    title: "High coverage requires manual review",
    steps: [
      planStep(
        "TP-101-ST-08",
        "Given",
        "the requested coverage exceeds the straight-through limit",
      ),
      planStep("TP-101-ST-09", "When", "the applicant submits the application"),
      planStep(
        "TP-101-ST-10",
        "Then",
        "the application requires additional review",
      ),
    ],
  },
];

function actions(
  stepId: string,
  definitions: readonly [ImplementationActionType, string, string][],
) {
  return definitions.map(([action, target, value], index) => ({
    id: `${stepId}-ACT-${String(index + 1).padStart(2, "0")}`,
    bddStepId: stepId,
    action,
    target,
    value,
  }));
}

function featureFromPlan(
  automationId: string,
  testPlanId: string | null,
  type: Automation["type"],
): BddFeature {
  return {
    title: "Life insurance application underwriting",
    scenarios: lifePlanScenarios.map((scenario, scenarioIndex) => ({
      id: `${automationId}-SC-${String(scenarioIndex + 1).padStart(2, "0")}`,
      title: scenario.title,
      sourceTestPlanScenarioId:
        testPlanId === null ? null : scenario.id.replace("TP-101", testPlanId),
      steps: scenario.steps.map((step, stepIndex) => {
        const id = `${automationId}-ST-${String(
          lifePlanScenarios
            .slice(0, scenarioIndex)
            .reduce((total, item) => total + item.steps.length, 0) +
            stepIndex +
            1,
        ).padStart(2, "0")}`;
        const webBindings =
          scenarioIndex === 0 && stepIndex === 0
            ? actions(id, [
                ["Navigate", "/life/applications/new", ""],
                ["Click", "button[data-testid='start-application']", ""],
              ])
            : scenarioIndex === 0 && stepIndex === 1
              ? actions(id, [
                  ["Click", "input[name='sumAssured']", ""],
                  ["Send keys", "input[name='sumAssured']", "1000000"],
                  [
                    "Send keys",
                    "textarea[name='healthDeclaration']",
                    "No disclosed conditions",
                  ],
                ])
              : scenarioIndex === 0 && stepIndex === 2
                ? actions(id, [
                    ["Click", "button[data-testid='submit-underwriting']", ""],
                  ])
                : scenarioIndex === 0 && stepIndex === 3
                  ? actions(id, [
                      [
                        "Assert",
                        "[data-testid='underwriting-status']",
                        "Pending underwriting",
                      ],
                    ])
                  : actions(id, [
                      ["Assert", "[data-testid='application-form']", step.text],
                    ]);
        const mobileBindings =
          scenarioIndex === 0 && stepIndex === 0
            ? actions(id, [["Wait", "application-ready", ""]])
            : scenarioIndex === 0 && stepIndex === 1
              ? actions(id, [
                  ["Click", "sum-assured-field", ""],
                  ["Send keys", "sum-assured-field", "1000000"],
                  [
                    "Send keys",
                    "health-declaration-field",
                    "No disclosed conditions",
                  ],
                ])
              : scenarioIndex === 0 && stepIndex === 2
                ? actions(id, [["Click", "submit-underwriting", ""]])
                : scenarioIndex === 0 && stepIndex === 3
                  ? actions(id, [
                      ["Assert", "underwriting-status", "Pending underwriting"],
                    ])
                  : actions(id, [["Assert", "application-screen", step.text]]);
        return {
          id,
          keyword: step.keyword,
          text: step.text,
          sourceTestPlanStepId:
            testPlanId === null ? null : step.id.replace("TP-101", testPlanId),
          actions: type === "web" ? webBindings : mobileBindings,
        };
      }),
    })),
  };
}

export function createLifeAutomation(
  id = "AUTO-101",
  testPlanId: string | null = "TP-101",
  source: Automation["source"] = "Manual",
  type: Automation["type"] = "web",
): Automation {
  return {
    id,
    title: "Life insurance application automation",
    goal: "Submit a complete life insurance application for underwriting",
    type,
    source,
    status: "ready",
    testPlanId,
    feature: featureFromPlan(id, testPlanId, type),
    supportedPlatforms: type === "mobile" ? ["ios", "android"] : [],
    revision: 1,
    updatedAt: "2026-09-03T09:00:00Z",
  };
}

export function createBlankAutomation({
  id,
  title,
  goal,
  type,
  testPlanId,
  source = "Manual",
}: {
  id: string;
  title: string;
  goal: string;
  type: Automation["type"];
  testPlanId: string | null;
  source?: Automation["source"];
}): Automation {
  const scenarioId = `${id}-SC-01`;
  const stepId = `${id}-ST-01`;
  return {
    id,
    title,
    goal,
    type,
    source,
    status: "draft",
    testPlanId,
    feature: {
      title,
      scenarios: [
        {
          id: scenarioId,
          title: "New scenario",
          sourceTestPlanScenarioId: null,
          steps: [
            {
              id: stepId,
              keyword: "Given",
              text: "Describe the starting context",
              sourceTestPlanStepId: null,
              actions: [],
            },
          ],
        },
      ],
    },
    supportedPlatforms: type === "mobile" ? ["ios", "android"] : [],
    revision: 1,
    updatedAt: "2026-09-03T09:10:00Z",
  };
}

export function createGeneratedAutomation({
  id,
  title,
  goal,
  type,
  testPlanId,
  source = "Manual",
}: {
  id: string;
  title: string;
  goal: string;
  type: Automation["type"];
  testPlanId: string | null;
  source?: Automation["source"];
}): Automation {
  const scenarioId = `${id}-SC-01`;
  const givenId = `${id}-ST-01`;
  const whenId = `${id}-ST-02`;
  const thenId = `${id}-ST-03`;
  return {
    id,
    title,
    goal,
    type,
    source,
    status: "draft",
    testPlanId,
    feature: {
      title,
      scenarios: [
        {
          id: scenarioId,
          title: goal,
          sourceTestPlanScenarioId: null,
          steps: [
            {
              id: givenId,
              keyword: "Given",
              text:
                type === "web"
                  ? "the browser is ready at the application start page"
                  : "the mobile application is ready on a supported device",
              sourceTestPlanStepId: null,
              actions:
                type === "web"
                  ? actions(givenId, [["Navigate", "/", ""]])
                  : actions(givenId, [["Wait", "application-ready", ""]]),
            },
            {
              id: whenId,
              keyword: "When",
              text: goal,
              sourceTestPlanStepId: null,
              actions: actions(whenId, [
                ["Click", "primary-action", ""],
                ["Send keys", "required-input", "sample value"],
              ]),
            },
            {
              id: thenId,
              keyword: "Then",
              text: "the expected outcome is shown",
              sourceTestPlanStepId: null,
              actions: actions(thenId, [
                ["Assert", "result-status", "Expected result"],
              ]),
            },
          ],
        },
      ],
    },
    supportedPlatforms: type === "mobile" ? ["ios", "android"] : [],
    revision: 1,
    updatedAt: "2026-09-03T09:10:00Z",
  };
}

export function createLifeTestPlan(
  id = "TP-101",
  automationId: string | null = "AUTO-101",
  source: TestPlan["source"] = "Manual",
): TestPlan {
  return {
    id,
    title: "Life insurance application underwriting",
    source,
    status: "ready",
    automationId,
    scenarios: lifePlanScenarios.map((scenario) => ({
      ...scenario,
      id: scenario.id.replace("TP-101", id),
      steps: scenario.steps.map((step) => ({
        ...step,
        id: step.id.replace("TP-101", id),
      })),
    })),
    updatedAt: "2026-09-03T09:00:00Z",
  };
}

export const executionAgents: readonly ExecutionAgent[] = [
  { id: "ado-web-agent-03", name: "ADO Web Agent 03", status: "online" },
  { id: "ado-web-agent-07", name: "ADO Web Agent 07", status: "busy" },
  { id: "ado-web-agent-12", name: "ADO Web Agent 12", status: "offline" },
];

export const mobileDevices: readonly MobileDevice[] = [
  { id: "iphone-15", name: "iPhone 15", platform: "ios", status: "available" },
  { id: "iphone-14", name: "iPhone 14", platform: "ios", status: "offline" },
  { id: "pixel-9", name: "Pixel 9", platform: "android", status: "available" },
  { id: "galaxy-s24", name: "Galaxy S24", platform: "android", status: "busy" },
];

export function createInitialArtifactState(): ArtifactState {
  const mobileStepId = "AUTO-102-ST-01";
  return {
    automations: [
      createLifeAutomation(),
      {
        id: "AUTO-102",
        title: "Claims photo upload",
        goal: "Upload a claim photo from a mobile device",
        type: "mobile",
        source: "Manual",
        status: "draft",
        testPlanId: null,
        feature: {
          title: "Claims evidence upload",
          scenarios: [
            {
              id: "AUTO-102-SC-01",
              title: "Upload a claim photo",
              sourceTestPlanScenarioId: null,
              steps: [
                {
                  id: mobileStepId,
                  keyword: "When",
                  text: "the claimant selects and uploads a photo",
                  sourceTestPlanStepId: null,
                  actions: actions(mobileStepId, [
                    ["Click", "upload-photo", ""],
                    ["Send keys", "photo-picker", "claim.jpg"],
                  ]),
                },
              ],
            },
          ],
        },
        supportedPlatforms: ["ios", "android"],
        revision: 1,
        updatedAt: "2026-09-03T09:05:00Z",
      },
    ],
    testPlans: [
      createLifeTestPlan(),
      {
        id: "TP-102",
        title: "Beneficiary designation validation",
        source: "Manual",
        status: "draft",
        automationId: null,
        scenarios: [
          {
            id: "TP-102-SC-01",
            title: "Require a valid beneficiary",
            steps: [
              planStep(
                "TP-102-ST-01",
                "Given",
                "a policy owner edits beneficiaries",
              ),
              planStep(
                "TP-102-ST-02",
                "Then",
                "at least one valid beneficiary is required",
              ),
            ],
          },
        ],
        updatedAt: "2026-09-03T09:10:00Z",
      },
    ],
    runs: [],
  };
}
