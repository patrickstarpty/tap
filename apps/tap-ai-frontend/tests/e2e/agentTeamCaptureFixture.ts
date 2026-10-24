import type { components } from "../../src/shared/api/generated/schema";

// Isolated, route-mocked fixture data for the Agent team `ui:capture`
// screenshots (team cards, org chart, Agent profile, My Agents, report).
// Pure data, no network or React imports. Response shapes follow the
// `AgentTeam*` schemas in `src/shared/api/generated/schema.ts`. Generic
// insurance-domain names only; actor ids are synthetic.

type Schemas = components["schemas"];
type Row = Schemas["AgentTeamMemberRow"];
type Trend = Schemas["AgentTeamTrendPoint"];
type Summary = Schemas["AgentTeamSummary"];
type Period = Schemas["AgentTeamPeriod"];

export const CAPTURE_TEAM_ME = "actor-capture";
const COMPUTED_AT = "2026-10-24T08:30:00Z";
const PERIOD = { from: "2026-09-25", to: "2026-10-24" };

function envelope<T>(data: T, period: Period = PERIOD) {
  return {
    runtimeStatus: "ok" as const,
    computedAt: COMPUTED_AT,
    timezone: "UTC",
    period,
    granularity: "day" as const,
    data,
  };
}

/** Thirty daily buckets ending 2026-10-24 with a deterministic wave. */
function trend(agentKey: string, base: number): Trend[] {
  return Array.from({ length: 30 }, (_, index) => {
    const date = new Date(Date.UTC(2026, 9, 24 - (29 - index)));
    const runs =
      base === 0 ? 0 : Math.max(0, base + ((index * 7) % 5) - 2 + (index % 3));
    return {
      agentKey,
      bucketStart: date.toISOString().slice(0, 10),
      costUsd: (runs * 0.012).toFixed(3),
      failures: runs > 0 && index % 9 === 4 ? 1 : 0,
      latencyTotalMs: runs * 2400,
      runs,
    };
  });
}

function row(partial: Partial<Row> & Pick<Row, "agentKey" | "name">): Row {
  const runs = partial.runs ?? 0;
  return {
    abstained: runs === 0 ? 0 : 2,
    callsPerRun: runs === 0 ? null : 3.2,
    canceled: runs === 0 ? 0 : 1,
    costUsd: (runs * 0.012).toFixed(2),
    failures: runs === 0 ? 0 : Math.round(runs * 0.03),
    generationCalls: runs * 2,
    jobTitle: null,
    joinedAt: "2026-08-12T09:00:00Z",
    kind: "custom",
    lastMaintenanceAt: null,
    lastRunAt: runs === 0 ? null : "2026-10-24T07:42:00Z",
    latencyP50Ms: runs === 0 ? null : 2100,
    latencyTotalMs: runs * 2400,
    maintenance: {},
    ownerActorId: null,
    pendingFeedback: 0,
    runs,
    satisfaction: runs === 0 ? null : 0.92,
    satisfactionSample: runs === 0 ? "insufficient" : "ok",
    servedCount: 0,
    status: runs === 0 ? "idle" : "active",
    toolCalls: runs,
    topServed: [],
    trend: trend(partial.agentKey, runs === 0 ? 0 : Math.round(runs / 30)),
    ...partial,
  };
}

const CLAIMS = row({
  agentKey: "agent_claims_reviewer",
  name: "Claims intake reviewer",
  jobTitle: "Claim intake reviewer",
  ownerActorId: CAPTURE_TEAM_ME,
  runs: 214,
  satisfaction: 0.94,
  servedCount: 3,
  topServed: [
    { actorId: "actor-lin", runs: 96 },
    { actorId: CAPTURE_TEAM_ME, runs: 71 },
    { actorId: "actor-maya", runs: 47 },
  ],
  pendingFeedback: 1,
  maintenance: { revised: 3, feedback_resolved: 2 },
  lastMaintenanceAt: "2026-10-20T14:05:00Z",
});
const DISCLOSURE = row({
  agentKey: "agent_disclosure_checker",
  name: "Disclosure checker",
  jobTitle: "Underwriting disclosure checker",
  ownerActorId: CAPTURE_TEAM_ME,
  runs: 88,
  satisfaction: 0.81,
  servedCount: 2,
  topServed: [
    { actorId: "actor-maya", runs: 55 },
    { actorId: CAPTURE_TEAM_ME, runs: 33 },
  ],
  maintenance: { revised: 1 },
  lastMaintenanceAt: "2026-10-12T10:30:00Z",
});
const UNDERWRITING = row({
  agentKey: "agent_underwriting_helper",
  name: "Underwriting helper",
  jobTitle: "Risk summary writer",
  ownerActorId: "actor-lin",
  runs: 142,
  satisfaction: 0.9,
  servedCount: 2,
  topServed: [
    { actorId: "actor-lin", runs: 100 },
    { actorId: "actor-maya", runs: 42 },
  ],
  maintenance: { revised: 4, skill_upgraded: 1 },
  lastMaintenanceAt: "2026-10-18T08:15:00Z",
});
const POLICY_FAQ = row({
  agentKey: "agent_policy_faq",
  name: "Policy FAQ guide",
  jobTitle: "Policy wording guide",
  ownerActorId: "actor-maya",
  runs: 0,
  servedCount: 0,
  maintenance: { revised: 1 },
  lastMaintenanceAt: "2026-09-02T11:00:00Z",
  lastRunAt: "2026-09-18T15:20:00Z",
});
const LEGACY = row({
  agentKey: "agent_legacy_summarizer",
  name: "Legacy case summarizer",
  jobTitle: "Retired case summarizer",
  ownerActorId: "actor-lin",
  runs: 0,
  status: "disabled",
  servedCount: 0,
  maintenance: { revised: 2, disabled: 1 },
  lastMaintenanceAt: "2026-09-28T16:40:00Z",
  lastRunAt: "2026-09-26T13:10:00Z",
});
const BUILT_IN = row({
  agentKey: "agent_builtin_life_underwriting_analyst",
  name: "Life Underwriting Analyst",
  kind: "built_in",
  jobTitle: "Life underwriting analyst",
  runs: 305,
  satisfaction: 0.88,
  servedCount: 3,
  topServed: [
    { actorId: "actor-maya", runs: 150 },
    { actorId: "actor-lin", runs: 90 },
    { actorId: CAPTURE_TEAM_ME, runs: 65 },
  ],
});
const DEFAULT = row({
  agentKey: "tapper_default",
  name: "Tapper",
  kind: "tapper_default",
  runs: 420,
  satisfaction: 0.86,
  servedCount: 3,
  topServed: [
    { actorId: CAPTURE_TEAM_ME, runs: 210 },
    { actorId: "actor-lin", runs: 130 },
    { actorId: "actor-maya", runs: 80 },
  ],
});

export const CAPTURE_TEAM_ROWS: Row[] = [
  CLAIMS,
  DISCLOSURE,
  UNDERWRITING,
  POLICY_FAQ,
  LEGACY,
  BUILT_IN,
  DEFAULT,
];

const PEOPLE: Schemas["AgentTeamPersonRow"][] = [
  {
    actorId: CAPTURE_TEAM_ME,
    maintenance: 6,
    ownedAgents: 2,
    ownedRuns: 302,
    runs: 379,
    servedByAgents: 5,
  },
  {
    actorId: "actor-lin",
    maintenance: 8,
    ownedAgents: 2,
    ownedRuns: 142,
    runs: 416,
    servedByAgents: 5,
  },
  {
    actorId: "actor-maya",
    maintenance: 1,
    ownedAgents: 1,
    ownedRuns: 0,
    runs: 374,
    servedByAgents: 5,
  },
];

const SUMMARY: Summary = {
  active: 4,
  agentTotal: 5,
  costUsd: "14.88",
  idle: 1,
  latencyTotalMs: 2_976_000,
  maintenanceTotal: 15,
  ownersAvg: 1.7,
  runs: 1_169,
  servedAvg: 2.6,
};

const PREVIOUS_SUMMARY: Summary = {
  active: 3,
  agentTotal: 5,
  costUsd: "11.20",
  idle: 2,
  latencyTotalMs: 2_320_000,
  maintenanceTotal: 11,
  ownersAvg: 1.7,
  runs: 930,
  servedAvg: 2.2,
};

const ORG_CHART: Schemas["AgentTeamOrgChart"] = {
  people: [CAPTURE_TEAM_ME, "actor-lin", "actor-maya"],
  agents: [
    CLAIMS.agentKey,
    DISCLOSURE.agentKey,
    UNDERWRITING.agentKey,
    POLICY_FAQ.agentKey,
    BUILT_IN.agentKey,
    DEFAULT.agentKey,
  ],
  edges: [
    { from: CAPTURE_TEAM_ME, to: CLAIMS.agentKey, kind: "owns" },
    { from: CAPTURE_TEAM_ME, to: DISCLOSURE.agentKey, kind: "owns" },
    { from: "actor-lin", to: UNDERWRITING.agentKey, kind: "owns" },
    { from: "actor-maya", to: POLICY_FAQ.agentKey, kind: "owns" },
    { from: CAPTURE_TEAM_ME, to: BUILT_IN.agentKey, kind: "serves" },
    { from: CAPTURE_TEAM_ME, to: DEFAULT.agentKey, kind: "serves" },
    { from: "actor-lin", to: CLAIMS.agentKey, kind: "serves" },
    { from: "actor-lin", to: BUILT_IN.agentKey, kind: "serves" },
    { from: "actor-maya", to: DISCLOSURE.agentKey, kind: "serves" },
    { from: "actor-maya", to: UNDERWRITING.agentKey, kind: "serves" },
    { from: "actor-maya", to: DEFAULT.agentKey, kind: "serves" },
  ],
};

const PENDING_FEEDBACK: Schemas["AgentTeamPendingFeedbackItem"][] = [
  {
    actorId: "actor-lin",
    agentKey: CLAIMS.agentKey,
    comment: "It skipped the incident date when the form had two policies.",
    conversationId: "conversation-capture-1",
    feedbackId: "feedback-capture-1",
    rating: "down",
    turnId: "turn-capture-1",
    updatedAt: "2026-10-23T16:12:00Z",
  },
];

const HISTORY: Schemas["AgentTeamHistoryPage"] = {
  events: [
    {
      actorId: CAPTURE_TEAM_ME,
      agentId: CLAIMS.agentKey,
      eventId: "event-capture-3",
      feedbackId: "feedback-capture-0",
      kind: "feedback_resolved",
      note: "Added the two-policy case to the checklist.",
      occurredAt: "2026-10-20T14:05:00Z",
      revisionId: null,
    },
    {
      actorId: CAPTURE_TEAM_ME,
      agentId: CLAIMS.agentKey,
      eventId: "event-capture-2",
      feedbackId: null,
      kind: "revised",
      note: null,
      occurredAt: "2026-10-20T13:50:00Z",
      revisionId: "agent-rev-capture-claims-3",
    },
    {
      actorId: CAPTURE_TEAM_ME,
      agentId: CLAIMS.agentKey,
      eventId: "event-capture-1",
      feedbackId: null,
      kind: "revised",
      note: null,
      occurredAt: "2026-09-14T09:20:00Z",
      revisionId: "agent-rev-capture-claims-2",
    },
  ],
  nextCursor: null,
};

function memberDetail(member: Row): Schemas["AgentTeamMemberDetail"] {
  return {
    row: member,
    currentRevisionId:
      member.kind === "tapper_default"
        ? null
        : `agent-rev-capture-${member.agentKey}`,
    served: member.topServed,
    pendingFeedback: PENDING_FEEDBACK.filter(
      (item) => item.agentKey === member.agentKey,
    ),
  };
}

function periodReport(
  summary: Summary,
  scale: number,
): Schemas["AgentTeamPeriodReport"] {
  return {
    members: CAPTURE_TEAM_ROWS.map((member) => ({
      ...member,
      runs: member.runs === null ? null : Math.round(member.runs * scale),
    })),
    people: PEOPLE.map((person) => ({
      ...person,
      runs: person.runs === null ? null : Math.round(person.runs * scale),
    })),
    summary,
  };
}

/**
 * Response body for an `/ai/agent-team/...` GET (the path after the project's
 * `/ai/` root), or `undefined` when the path is not an Agent team endpoint
 * this fixture backs.
 */
export function captureAgentTeamResponse(path: string): unknown {
  const match = /\/ai\/agent-team\/(.+)$/u.exec(path);
  if (match === null) return undefined;
  const rest = match[1]!;
  if (rest === "summary")
    return envelope(SUMMARY) satisfies Schemas["AgentTeamSummaryView"];
  if (rest === "members")
    return envelope(
      CAPTURE_TEAM_ROWS,
    ) satisfies Schemas["AgentTeamMembersView"];
  if (rest === "people")
    return envelope(PEOPLE) satisfies Schemas["AgentTeamPeopleView"];
  if (rest === "org-chart")
    return envelope(ORG_CHART) satisfies Schemas["AgentTeamOrgChartView"];
  if (rest === "people/me") {
    const owned = CAPTURE_TEAM_ROWS.filter(
      (member) => member.ownerActorId === CAPTURE_TEAM_ME,
    );
    const served = CAPTURE_TEAM_ROWS.filter((member) =>
      member.topServed.some((person) => person.actorId === CAPTURE_TEAM_ME),
    );
    return envelope({
      person: PEOPLE[0],
      owned,
      served,
      pendingFeedback: PENDING_FEEDBACK.filter((item) =>
        owned.some((member) => member.agentKey === item.agentKey),
      ),
    }) satisfies Schemas["AgentTeamPersonView"];
  }
  if (rest === "report") {
    return envelope(
      {
        current: periodReport(SUMMARY, 1),
        previous: periodReport(PREVIOUS_SUMMARY, 0.8),
        previousPeriod: { from: "2026-08-01", to: "2026-08-31" },
      },
      { from: "2026-09-01", to: "2026-09-30" },
    ) satisfies Schemas["AgentTeamReportView"];
  }
  const history = /^members\/([^/]+)\/history$/u.exec(rest);
  if (history !== null) return HISTORY;
  const member = /^members\/([^/]+)$/u.exec(rest);
  if (member !== null) {
    const key = decodeURIComponent(member[1]!);
    const found = CAPTURE_TEAM_ROWS.find((item) => item.agentKey === key);
    if (found !== undefined)
      return envelope(
        memberDetail(found),
      ) satisfies Schemas["AgentTeamMemberView"];
  }
  return undefined;
}
