import type { components } from "../../src/shared/api/generated/schema";

// Isolated, route-mocked fixture data for the Skill Hub `ui:capture`
// screenshots (library, Skill detail usage tab, Agent form pinned/excluded
// pickers, answer "Skills used" rows). Pure data, no network or React
// imports. Response shapes follow the `SkillHub*`, `SkillRevision*`,
// `AiAgentRevision*` and `Conversation*` schemas in
// `src/shared/api/generated/schema.ts`. Generic insurance-domain names
// only; ids and actor ids are synthetic.

type Schemas = components["schemas"];
type HubSkill = Schemas["SkillHubSkill"];
type HubUsage = Schemas["SkillHubUsage"];
type ModeCounts = Schemas["SkillHubModeCounts"];
type UsagePoint = Schemas["SkillHubUsagePoint"];
type Summary = Schemas["SkillHubLibrarySummary"];
type RevisionSummary = Schemas["SkillRevisionSummary"];
type RevisionDetail = Schemas["SkillRevisionDetail"];
type Agent = Schemas["AiAgentRevisionSummary"];
type Mode = UsagePoint["mode"];

const ME = "actor-capture";
const COMPUTED_AT = "2026-11-01T08:30:00Z";
const PERIOD = { from: "2026-10-03", to: "2026-11-01" };
const PERIOD_DAYS = 30;

function envelope<T>(data: T) {
  return {
    runtimeStatus: "ok" as const,
    computedAt: COMPUTED_AT,
    timezone: "UTC",
    period: PERIOD,
    granularity: "day" as const,
    data,
  };
}

/** A Skill Hub record with neutral defaults for whatever `partial` omits. */
export function captureHubSkill(
  partial: Partial<HubSkill> &
    Pick<HubSkill, "skillId" | "name" | "latestRevisionId">,
): HubSkill {
  return {
    applicableTasks: ["knowledge.answer"],
    builtIn: false,
    category: "other",
    compatibility: null,
    description: null,
    originImportedAt: null,
    originKind: "authored",
    originRef: null,
    originActorId: ME,
    ownerActorId: ME,
    ownerAssignedAt: "2026-08-12T09:00:00Z",
    revisionNumber: 1,
    sourceKind: "authored",
    status: "enabled",
    updatedAt: "2026-10-20T10:00:00Z",
    version: "r1",
    ...partial,
  };
}

interface CaptureSkillEntry {
  skill: HubSkill;
  usage: HubUsage;
  points: UsagePoint[];
  detail: Omit<Schemas["SkillHubDetail"], "skill" | "usage">;
}

export interface CaptureSkillLibrary {
  entries: CaptureSkillEntry[];
  summary: Summary;
}

/** Thirty daily buckets for one mode, ending on the period's last day, with
 * a deterministic wave; zero-run buckets are omitted as the API does. */
function modePoints(mode: Mode, base: number): UsagePoint[] {
  if (base === 0) return [];
  return Array.from({ length: PERIOD_DAYS }, (_, index) => {
    const date = new Date(Date.UTC(2026, 10, 1 - (PERIOD_DAYS - 1 - index)));
    return {
      bucketStart: date.toISOString().slice(0, 10),
      mode,
      runs: Math.max(
        0,
        base - 1 + ((index * 7) % 4) - (index % 5 === 0 ? 1 : 0),
      ),
    };
  }).filter((point) => point.runs > 0);
}

function sumRuns(points: UsagePoint[], mode: Mode): number {
  return points
    .filter((point) => point.mode === mode)
    .reduce((total, point) => total + point.runs, 0);
}

interface EntryInput {
  skill: HubSkill;
  bases: ModeCounts;
  users: number;
  satisfaction: number | null;
  lastUsedAt: string | null;
  pinnedBy?: string[];
  topAgents?: Schemas["SkillHubRankedKey"][];
  topCallers?: Schemas["SkillHubRankedKey"][];
  files?: Schemas["SkillFileView"][];
}

function entry(input: EntryInput): CaptureSkillEntry {
  const points = [
    ...modePoints("manual", input.bases.manual),
    ...modePoints("pinned", input.bases.pinned),
    ...modePoints("auto", input.bases.auto),
  ];
  const byMode: ModeCounts = {
    manual: sumRuns(points, "manual"),
    pinned: sumRuns(points, "pinned"),
    auto: sumRuns(points, "auto"),
  };
  const runs = byMode.manual + byMode.pinned + byMode.auto;
  const pinnedBy = input.pinnedBy ?? [];
  return {
    skill: input.skill,
    points,
    usage: {
      byMode,
      lastUsedAt: input.lastUsedAt,
      pinnedByAgents: pinnedBy.length,
      runtime: {
        costUsd: (runs * 0.004).toFixed(2),
        failures: runs > 40 ? 1 : 0,
        latencyTotalMs: runs * 2300,
        runs,
      },
      satisfaction: input.satisfaction,
      satisfactionSample: input.satisfaction === null ? "insufficient" : "ok",
      users: input.users,
    },
    detail: {
      files: input.files ?? [
        {
          relativePath: "SKILL.md",
          sizeBytes: 640,
          sha256: "e".repeat(64),
          category: "skill",
          attached: false,
        },
      ],
      pinnedBy,
      revisions: [],
      topAgents: input.topAgents ?? [],
      topCallers: input.topCallers ?? [],
    },
  };
}

export const CAPTURE_AGENT_CLAIMS = "agent-capture-claims";
const AGENT_DISCLOSURE = "agent-capture-disclosure";

const CLAIM_INTAKE = captureHubSkill({
  skillId: "skill-capture-claim-intake",
  latestRevisionId: "skill-rev-capture-claim-intake-3",
  name: "claim-intake-checklist",
  description:
    "Checks a claim submission for required fields before a reviewer looks at it.",
  category: "knowledge",
  sourceKind: "imported",
  originKind: "zip",
  originRef: "claim-intake-checklist.zip",
  originImportedAt: "2026-10-18T03:20:00Z",
  ownerAssignedAt: "2026-09-02T06:10:00Z",
  compatibility: "Requires a published claims handbook.",
  revisionNumber: 3,
  version: "1.2.0",
  updatedAt: "2026-10-18T03:20:00Z",
});
const TEST_DESIGN = captureHubSkill({
  skillId: "skill-capture-underwriting-tests",
  latestRevisionId: "skill-rev-capture-underwriting-tests-2",
  name: "underwriting-test-design",
  description:
    "Designs boundary and negative test cases from underwriting rules.",
  category: "test-design",
  applicableTasks: ["test-plan.generate"],
  sourceKind: "imported",
  originKind: "files",
  originRef: "underwriting-test-design/",
  originImportedAt: "2026-09-21T02:00:00Z",
  originActorId: "actor-lin",
  ownerActorId: "actor-lin",
  revisionNumber: 2,
  version: "2.0.1",
  updatedAt: "2026-10-09T07:45:00Z",
});
const CITATION = captureHubSkill({
  skillId: "validation-citation-skill",
  latestRevisionId: "validation-citation-skill-v2",
  name: "Citation skill",
  description: "Cites the source passage behind every statement in an answer.",
  category: "knowledge",
  builtIn: true,
  sourceKind: "built_in",
  originKind: "built_in",
  originActorId: "system",
  ownerActorId: "system",
  revisionNumber: 2,
  version: "r2",
  updatedAt: "2026-08-01T00:00:00Z",
});
const AUTOMATION = captureHubSkill({
  skillId: "skill-capture-policy-api",
  latestRevisionId: "skill-rev-capture-policy-api-4",
  name: "policy-api-automation",
  description:
    "Turns policy servicing API cases into runnable automation steps.",
  category: "automation",
  applicableTasks: ["automation.generate"],
  originActorId: "actor-maya",
  ownerActorId: "actor-maya",
  revisionNumber: 4,
  version: "r4",
  updatedAt: "2026-10-27T09:10:00Z",
});
const ANALYSIS = captureHubSkill({
  skillId: "skill-capture-claim-trends",
  latestRevisionId: "skill-rev-capture-claim-trends-1",
  name: "claim-trend-analysis",
  description: "Summarises claim volume and rejection reasons across a period.",
  category: "analysis",
  sourceKind: "imported",
  originKind: "paste",
  originImportedAt: "2026-10-05T04:30:00Z",
  version: "0.3.0",
  updatedAt: "2026-10-05T04:30:00Z",
});
const WRITING = captureHubSkill({
  skillId: "skill-capture-customer-letter",
  latestRevisionId: "skill-rev-capture-customer-letter-1",
  name: "customer-letter-writer",
  description: "Drafts plain-language customer letters about a claim decision.",
  category: "writing",
  originActorId: "actor-lin",
  ownerActorId: "actor-lin",
  updatedAt: "2026-10-14T11:00:00Z",
});
const LEGACY = captureHubSkill({
  skillId: "skill-capture-legacy-summary",
  latestRevisionId: "skill-rev-capture-legacy-summary-1",
  name: "legacy-claim-summary",
  description: "Summarises claim files in the retired case format.",
  category: "other",
  status: "disabled",
  sourceKind: "imported",
  originKind: "files",
  originRef: "legacy-claim-summary/",
  originImportedAt: "2026-06-30T08:00:00Z",
  version: "1.0.0",
  updatedAt: "2026-09-12T15:30:00Z",
});

const CLAIM_INTAKE_FILES: Schemas["SkillFileView"][] = [
  {
    relativePath: "SKILL.md",
    sizeBytes: 420,
    sha256: "a".repeat(64),
    category: "skill",
    attached: false,
  },
  {
    relativePath: "references/glossary.md",
    sizeBytes: 180,
    sha256: "b".repeat(64),
    category: "reference",
    attached: true,
  },
  {
    relativePath: "scripts/check_fields.py",
    sizeBytes: 260,
    sha256: "c".repeat(64),
    category: "script",
    attached: false,
  },
  {
    relativePath: "assets/template.txt",
    sizeBytes: 90,
    sha256: "d".repeat(64),
    category: "asset",
    attached: false,
  },
];

const ENTRIES: CaptureSkillEntry[] = [
  entry({
    skill: CLAIM_INTAKE,
    bases: { manual: 1, pinned: 3, auto: 2 },
    users: 9,
    // Few people rated these answers: the panel shows "Insufficient sample".
    satisfaction: null,
    lastUsedAt: "2026-11-01T07:42:00Z",
    pinnedBy: [CAPTURE_AGENT_CLAIMS],
    topAgents: [
      { key: CAPTURE_AGENT_CLAIMS, runs: 64 },
      { key: "tapper_default", runs: 41 },
      { key: AGENT_DISCLOSURE, runs: 12 },
    ],
    topCallers: [
      { key: "actor-lin", runs: 52 },
      { key: ME, runs: 38 },
      { key: "actor-maya", runs: 27 },
    ],
    files: CLAIM_INTAKE_FILES,
  }),
  entry({
    skill: TEST_DESIGN,
    bases: { manual: 3, pinned: 0, auto: 1 },
    users: 4,
    satisfaction: 0.91,
    lastUsedAt: "2026-10-31T09:15:00Z",
  }),
  entry({
    skill: CITATION,
    bases: { manual: 0, pinned: 0, auto: 3 },
    users: 12,
    satisfaction: 0.88,
    lastUsedAt: "2026-11-01T08:05:00Z",
  }),
  entry({
    skill: AUTOMATION,
    bases: { manual: 2, pinned: 0, auto: 0 },
    users: 2,
    satisfaction: 0.8,
    lastUsedAt: "2026-10-29T13:20:00Z",
  }),
  entry({
    skill: ANALYSIS,
    bases: { manual: 1, pinned: 0, auto: 1 },
    users: 3,
    satisfaction: 0.86,
    lastUsedAt: "2026-10-30T16:40:00Z",
    pinnedBy: [AGENT_DISCLOSURE],
  }),
  entry({
    skill: WRITING,
    bases: { manual: 1, pinned: 0, auto: 0 },
    users: 2,
    satisfaction: null,
    lastUsedAt: "2026-10-28T10:00:00Z",
  }),
  entry({
    skill: LEGACY,
    bases: { manual: 0, pinned: 0, auto: 0 },
    users: 0,
    satisfaction: null,
    lastUsedAt: "2026-09-10T12:00:00Z",
  }),
];

function activations(item: CaptureSkillEntry): number {
  const { manual, pinned, auto } = item.usage.byMode;
  return manual + pinned + auto;
}

/** The populated library: every category, a built-in and a disabled Skill,
 * and 61 automatic candidates against the limit of 60 so the library shows
 * its "can't offer all of them automatically" notice. */
export const CAPTURE_SKILL_LIBRARY: CaptureSkillLibrary = {
  // The library's default sort is "Most activations".
  entries: [...ENTRIES].sort((a, b) => activations(b) - activations(a)),
  summary: {
    autoShare: 0.38,
    candidateCount: 61,
    candidateLimit: 60,
    enabled: 63,
    skillTotal: 64,
    unused: 23,
    usedInPeriod: 41,
  },
};

/** A library holding exactly `skills`, none used in the period. */
export function captureSkillLibraryOf(
  skills: readonly HubSkill[],
): CaptureSkillLibrary {
  const entries = skills.map((skill) =>
    entry({
      skill,
      bases: { manual: 0, pinned: 0, auto: 0 },
      users: 0,
      satisfaction: null,
      lastUsedAt: null,
    }),
  );
  const enabled = skills.filter((skill) => skill.status === "enabled").length;
  return {
    entries,
    summary: {
      autoShare: null,
      candidateCount: enabled,
      candidateLimit: 60,
      enabled,
      skillTotal: skills.length,
      unused: skills.length,
      usedInPeriod: 0,
    },
  };
}

/**
 * Response body for an `/ai/skill-hub/...` GET backed by `library`, or
 * `undefined` when the path is not a Skill Hub view this fixture backs.
 * Catalog filters and sort parameters are ignored: captures use the
 * library's default view.
 */
export function captureSkillHubResponse(
  path: string,
  library: CaptureSkillLibrary = CAPTURE_SKILL_LIBRARY,
): unknown {
  const match = /\/ai\/skill-hub\/(.+)$/u.exec(path);
  if (match === null) return undefined;
  const rest = match[1]!;
  if (rest === "summary")
    return envelope(library.summary) satisfies Schemas["SkillHubSummaryView"];
  if (rest === "catalog")
    return envelope(
      library.entries.map((item) => ({
        activations: activations(item),
        byMode: item.usage.byMode,
        skill: item.skill,
      })),
    ) satisfies Schemas["SkillHubCatalogView"];
  const usage = /^catalog\/([^/]+)\/usage$/u.exec(rest);
  const detail = /^catalog\/([^/]+)$/u.exec(rest);
  const skillId = decodeURIComponent((usage ?? detail)?.[1] ?? "");
  const found = library.entries.find((item) => item.skill.skillId === skillId);
  if (found === undefined) return undefined;
  if (usage !== null)
    return envelope({
      byMode: found.usage.byMode,
      points: found.points,
    }) satisfies Schemas["SkillHubUsageView"];
  return envelope({
    ...found.detail,
    revisions: revisionsOf(found.skill),
    skill: found.skill,
    usage: found.usage,
  }) satisfies Schemas["SkillHubDetailView"];
}

// -- AI asset endpoints behind the same Skills ----------------------------

function revisionSummary(
  skill: HubSkill,
  revisionNumber: number = skill.revisionNumber,
): RevisionSummary {
  const latest = revisionNumber === skill.revisionNumber;
  return {
    applicableTasks:
      skill.applicableTasks as RevisionSummary["applicableTasks"],
    assetId: skill.skillId,
    builtIn: skill.builtIn,
    category: skill.category,
    contentDigest: `sha256:${skill.skillId}-${String(revisionNumber)}`,
    description: skill.description,
    displayName: skill.name,
    isLatest: latest,
    revisionId: latest
      ? skill.latestRevisionId
      : `${skill.skillId}-rev-${String(revisionNumber)}`,
    skillId: skill.skillId,
    sourceKind: skill.sourceKind,
    status: skill.status,
    versionLabel: latest ? skill.version : null,
  };
}

/** Oldest first, as `GET /ai/skills/{skillId}/revisions` returns them. */
function revisionsOf(skill: HubSkill): RevisionSummary[] {
  return Array.from({ length: skill.revisionNumber }, (_, index) =>
    revisionSummary(skill, index + 1),
  );
}

function revisionDetail(item: CaptureSkillEntry): RevisionDetail {
  const summary = revisionSummary(item.skill);
  return {
    ...summary,
    attachedReferences:
      item.skill === CLAIM_INTAKE
        ? "## Glossary\n\nIncident date: the calendar date the loss occurred."
        : null,
    files: item.detail.files,
    frontmatter: {
      name: item.skill.name,
      description: item.skill.description ?? "",
    },
    instructions: `${item.skill.description ?? ""}\n\nFollow the steps in order and state any missing input.`,
  };
}

const AGENTS: Agent[] = [
  {
    agentId: CAPTURE_AGENT_CLAIMS,
    assetId: CAPTURE_AGENT_CLAIMS,
    builtIn: false,
    contentDigest: "sha256:agent-capture-claims-3",
    description:
      "Reviews claim intake submissions against the required fields.",
    displayName: "Claims intake reviewer",
    excludedSkillIds: [WRITING.skillId],
    isLatest: true,
    outputSchemaDigest: "sha256:agent-output",
    pinnedSkillIds: [CLAIM_INTAKE.skillId],
    revisionId: "agent-rev-capture-claims-3",
    sourceKind: "authored",
    status: "enabled",
    toolAllowlist: ["knowledge.search", "knowledge.answer"],
  },
  {
    agentId: AGENT_DISCLOSURE,
    assetId: AGENT_DISCLOSURE,
    builtIn: false,
    contentDigest: "sha256:agent-capture-disclosure-1",
    description: "Checks underwriting disclosures for missing answers.",
    displayName: "Disclosure checker",
    excludedSkillIds: [],
    isLatest: true,
    outputSchemaDigest: "sha256:agent-output",
    pinnedSkillIds: [ANALYSIS.skillId],
    revisionId: "agent-rev-capture-disclosure-1",
    sourceKind: "authored",
    status: "enabled",
    toolAllowlist: ["knowledge.search", "knowledge.answer"],
  },
];

const TOOLS: Schemas["ToolDescriptor"][] = [
  {
    toolId: "knowledge.search",
    name: "Knowledge search",
    description: "Searches the selected knowledge sources.",
    required: true,
    defaultEnabled: true,
  },
  {
    toolId: "knowledge.answer",
    name: "Knowledge answer",
    description: "Answers from the retrieved passages with citations.",
    required: true,
    defaultEnabled: true,
  },
  {
    toolId: "graph.relations",
    name: "Knowledge graph relations",
    description: "Looks up how the concepts in a question are related.",
    required: false,
    defaultEnabled: true,
  },
];

// -- A conversation whose answer used one Skill in each mode ---------------

export const CAPTURE_SKILLS_CONVERSATION_TITLE = "Hospital cash claim intake";
export const CAPTURE_SKILLS_QUESTION =
  "What must a hospital cash claim list before it is routed for review?";
const CONVERSATION_ID = "conversation-skills";
const TURN_ID = "turn-skills";
const SOURCE = {
  sourceId: "source-claims",
  documentId: "document-claims",
  documentRevisionId: "document-rev-claims",
  label: "Claims handbook",
};
const ANSWER =
  "A hospital cash claim must list the claimant name, policy number and incident date before it is routed for review.";

const CONVERSATION_SUMMARY: Schemas["ConversationSummary"] = {
  conversationId: CONVERSATION_ID,
  title: CAPTURE_SKILLS_CONVERSATION_TITLE,
  createdAt: "2026-11-01T07:40:00Z",
  updatedAt: "2026-11-01T07:42:00Z",
};

const CONVERSATION_DETAIL: Schemas["ConversationDetail"] = {
  ...CONVERSATION_SUMMARY,
  turns: [
    {
      turnId: TURN_ID,
      state: "completed",
      attempt: 1,
      traceId: null,
      inputSnapshotDigest: `sha256:${"f".repeat(64)}`,
      answerEvidenceSnapshotDigest: null,
      input: {
        message: CAPTURE_SKILLS_QUESTION,
        modelAlias: "qwen-plus",
        sourceRevisionIds: [SOURCE.documentRevisionId],
        documentRevisionIds: [SOURCE.documentRevisionId],
        resolvedResources: [
          { ...SOURCE, sourceRevisionId: SOURCE.documentRevisionId },
        ],
        agentRevisionId: AGENTS[0]!.revisionId,
        agentLabel: AGENTS[0]!.displayName,
        skillRevisionIds: [
          WRITING.latestRevisionId,
          CLAIM_INTAKE.latestRevisionId,
        ],
        skillLabels: [WRITING.name, CLAIM_INTAKE.name],
        skillsUsed: [
          {
            skillId: WRITING.skillId,
            revisionId: WRITING.latestRevisionId,
            label: WRITING.name,
            mode: "manual",
          },
          {
            skillId: CLAIM_INTAKE.skillId,
            revisionId: CLAIM_INTAKE.latestRevisionId,
            label: CLAIM_INTAKE.name,
            mode: "pinned",
          },
          {
            skillId: CITATION.skillId,
            revisionId: CITATION.latestRevisionId,
            label: CITATION.name,
            mode: "auto",
          },
        ],
        insightsQueryId: null,
      },
    },
  ],
};

const CONVERSATION_EVENTS: Schemas["ConversationEventPage"] = {
  items: [
    {
      eventId: "event-skills-context",
      sequence: 1,
      turnId: TURN_ID,
      occurredAt: "2026-11-01T07:41:00Z",
      eventType: "context.assembled",
      payload: { contextSnapshotId: "context-skills", tokenCount: 640 },
    },
    {
      eventId: "event-skills-completed",
      sequence: 2,
      turnId: TURN_ID,
      occurredAt: "2026-11-01T07:42:00Z",
      eventType: "turn.completed",
      payload: {
        answer: {
          traceId: null,
          queryPlanId: "plan-skills",
          contextSnapshotId: "context-skills",
          corpusVersion: "v1",
          retrievalProfileId: "quick",
          degradedMode: false,
          answer: ANSWER,
          abstained: false,
          claims: [
            {
              claimId: "claim-skills-1",
              text: ANSWER,
              answerStart: 0,
              answerEnd: ANSWER.length,
              citationIds: ["c1"],
            },
          ],
          citations: [
            {
              citationId: "c1",
              evidenceLabel: "S1",
              chunkId: "chunk-claims-1",
              logicalChunkId: "logical-claims-1",
              source: {
                sourceId: SOURCE.sourceId,
                sourceType: "document",
                revisionKind: "blob_version",
                revision: SOURCE.documentRevisionId,
                sourceContentHash: `sha256:${"a".repeat(64)}`,
                anchor: {
                  type: "document",
                  headingPath: ["Claim intake"],
                  page: 1,
                  bbox: null,
                  startOffset: 0,
                  endOffset: 96,
                },
              },
              chunkContentHash: `sha256:${"b".repeat(64)}`,
              contentRole: "source",
              derivedFromChunkIds: null,
            },
          ],
        },
      },
    },
  ],
};

/**
 * Response body for the AI asset and conversation GETs the Skill Hub
 * captures read alongside `captureSkillHubResponse` (`/ai/skills...`,
 * `/ai/agents`, `/ai/tools`, `/conversations...`), or `undefined` when the
 * path is not one this fixture backs.
 */
export function captureSkillAssetsResponse(path: string): unknown {
  const entries = CAPTURE_SKILL_LIBRARY.entries;
  if (path.endsWith("/ai/skills"))
    return {
      items: entries.map((item) => revisionSummary(item.skill)),
    } satisfies Schemas["SkillRevisionPage"];
  const revisions = /\/ai\/skills\/([^/]+)\/revisions$/u.exec(path);
  if (revisions !== null) {
    const skillId = decodeURIComponent(revisions[1]!);
    const found = entries.find((item) => item.skill.skillId === skillId);
    return found === undefined
      ? undefined
      : ({
          items: revisionsOf(found.skill),
        } satisfies Schemas["SkillRevisionPage"]);
  }
  const skill = /\/ai\/skills\/([^/]+)$/u.exec(path);
  if (skill !== null) {
    const revisionId = decodeURIComponent(skill[1]!);
    const found = entries.find(
      (item) => item.skill.latestRevisionId === revisionId,
    );
    return found === undefined
      ? undefined
      : (revisionDetail(found) satisfies RevisionDetail);
  }
  if (path.endsWith("/ai/agents"))
    return { items: AGENTS } satisfies Schemas["AiAgentRevisionPage"];
  if (path.endsWith("/ai/tools"))
    return { items: TOOLS } satisfies Schemas["ToolPage"];
  if (path.endsWith("/conversations"))
    return {
      items: [CONVERSATION_SUMMARY],
      nextCursor: null,
    } satisfies Schemas["ConversationPage"];
  if (path.endsWith(`/conversations/${CONVERSATION_ID}`))
    return CONVERSATION_DETAIL;
  if (path.endsWith(`/conversations/${CONVERSATION_ID}/events`))
    return CONVERSATION_EVENTS;
  return undefined;
}
