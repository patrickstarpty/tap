import { useState, type KeyboardEvent } from "react";

import type {
  AgentTeamMemberRow,
  AgentTeamOrgChartView,
} from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";
import { avatarTone, initialOf } from "./avatar";
import { formatCompact } from "./format";

/** What a node shows besides its name. */
export interface OrgAgentStat {
  name: string;
  runs: number | null;
  status: AgentTeamMemberRow["status"];
}

export interface OrgChartProps {
  chart: AgentTeamOrgChartView["data"];
  agentStats: Record<string, OrgAgentStat>;
  /** Calls each person made in the period (null: run data unknown). */
  personRuns?: Record<string, number | null>;
  copy: WorkspaceCopy;
  locale?: string;
  onOpenMember: (agentKey: string) => void;
}

const PAD = 16;
const COL_W = 212;
const COL_GAP = 28;
const HEAD_H = 44;
const TRUNK_X = 14;
const INDENT = 28;
const NODE_H = 34;
const NODE_GAP = 8;
const PILL_W = 164;
const PILL_H = 32;
const PILL_GAP = 10;
const BAND_GAP = 44;
const COUNT_W = 34;

function activate(action: () => void) {
  return (event: KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      action();
    }
  };
}

/** Rough text fit for SVG labels: CJK glyphs are about twice as wide. */
function fit(label: string, maxPx: number, size: number): string {
  let width = 0;
  const chars = Array.from(label);
  for (let i = 0; i < chars.length; i += 1) {
    width += (chars[i].charCodeAt(0) > 0x2e80 ? 1 : 0.58) * size;
    if (width > maxPx) return `${chars.slice(0, Math.max(i - 1, 1)).join("")}…`;
  }
  return label;
}

interface Point {
  x: number;
  y: number;
}

/** Ownership as a tree (one column per owner, plus the platform-maintained
 * group); usage is drawn only for the person the reader selects. */
export function OrgChart({
  chart,
  agentStats,
  personRuns = {},
  copy,
  locale = "en",
  onOpenMember,
}: OrgChartProps) {
  const labels = copy.agentTeam.team;
  const [selected, setSelected] = useState<string | null>(null);
  if (chart.edges.length === 0)
    return <p className="tap-team-empty">{labels.orgChartEmpty}</p>;

  const agentLabel = (key: string) =>
    agentStats[key]?.name ??
    (key === "tapper_default" ? labels.kinds.tapper_default : key);
  const countText = (value: number | null | undefined) =>
    value === null || value === undefined ? "" : formatCompact(value, locale);

  // Group agents under their owner; unowned (built-in, Tapper default) go to
  // the platform-maintained group.
  const ownerOf = new Map<string, string>();
  for (const edge of chart.edges)
    if (edge.kind === "owns") ownerOf.set(edge.to, edge.from);
  const owners = chart.people.filter((id) =>
    chart.agents.some((agent) => ownerOf.get(agent) === id),
  );
  const users = chart.people.filter((id) => !owners.includes(id));
  const platform = chart.agents.filter((agent) => !ownerOf.has(agent));
  const groups: { owner: string | null; agents: string[] }[] = [
    ...owners.map((owner) => ({
      owner,
      agents: chart.agents.filter((agent) => ownerOf.get(agent) === owner),
    })),
    ...(platform.length > 0 ? [{ owner: null, agents: platform }] : []),
  ];

  const colX = (i: number) => PAD + i * (COL_W + COL_GAP);
  const agentTop = (j: number) => PAD + HEAD_H + 16 + j * (NODE_H + NODE_GAP);
  const agentBox = new Map<string, { x: number; y: number; col: number }>();
  groups.forEach((group, i) =>
    group.agents.forEach((agent, j) =>
      agentBox.set(agent, { x: colX(i) + INDENT, y: agentTop(j), col: i }),
    ),
  );
  const tallest = Math.max(...groups.map((g) => g.agents.length), 0);
  const treeBottom = agentTop(tallest) - NODE_GAP;
  const groupsWidth =
    PAD * 2 + groups.length * COL_W + (groups.length - 1) * COL_GAP;
  const width = Math.max(groupsWidth, PAD * 2 + 4 * (PILL_W + PILL_GAP));
  const perRow = Math.max(
    1,
    Math.floor((width - PAD * 2 + PILL_GAP) / (PILL_W + PILL_GAP)),
  );
  const bandTop = treeBottom + BAND_GAP;
  const pillPos = (i: number): Point => ({
    x: PAD + (i % perRow) * (PILL_W + PILL_GAP),
    y: bandTop + 22 + Math.floor(i / perRow) * (PILL_H + PILL_GAP),
  });
  const height =
    users.length === 0
      ? treeBottom + PAD
      : pillPos(users.length - 1).y + PILL_H + PAD;

  // Usage lines leave an owner from the side facing the Agent (the header
  // row holds only owners, so nothing is crossed there) and a pill from its
  // top; they enter an Agent at its nearer side.
  const ownerColumn = new Map<string, number>();
  groups.forEach((group, i) => {
    if (group.owner !== null) ownerColumn.set(group.owner, i);
  });
  const userIndex = new Map(users.map((id, i) => [id, i]));
  const personAnchor = (id: string, target: string): Point | null => {
    const box = agentBox.get(target);
    if (box === undefined) return null;
    const col = ownerColumn.get(id);
    if (col !== undefined) {
      const y = PAD + HEAD_H / 2;
      if (box.col > col) return { x: colX(col) + COL_W, y };
      if (box.col < col) return { x: colX(col), y };
      return { x: colX(col) + COL_W / 2, y: PAD + HEAD_H };
    }
    const i = userIndex.get(id);
    if (i === undefined) return null;
    const p = pillPos(i);
    return { x: p.x + PILL_W / 2, y: p.y };
  };
  const agentAnchor = (key: string, from: Point): Point | null => {
    const box = agentBox.get(key);
    if (box === undefined) return null;
    const w = COL_W - INDENT;
    return from.x <= box.x + w / 2
      ? { x: box.x, y: box.y + NODE_H / 2 }
      : { x: box.x + w, y: box.y + NODE_H / 2 };
  };

  const related = new Set<string>();
  if (selected !== null)
    for (const edge of chart.edges)
      if (edge.from === selected) related.add(edge.to);
  const personDimmed = (id: string) => selected !== null && selected !== id;
  const agentDimmed = (key: string) => selected !== null && !related.has(key);

  const personNode = (
    id: string,
    x: number,
    y: number,
    w: number,
    h: number,
    pill: boolean,
  ) => {
    const toggle = () => setSelected(selected === id ? null : id);
    const tone = avatarTone(id);
    const r = pill ? 10 : 12;
    const count = countText(personRuns[id]);
    return (
      <g
        key={id}
        role="button"
        tabIndex={0}
        className="tap-org-node tap-org-person"
        aria-pressed={selected === id}
        aria-label={id}
        aria-description={personDimmed(id) ? labels.orgChartDimmed : undefined}
        data-dimmed={personDimmed(id) ? "true" : "false"}
        onClick={toggle}
        onKeyDown={activate(toggle)}
      >
        <title>{id}</title>
        <rect x={x} y={y} width={w} height={h} rx={pill ? h / 2 : 12} />
        <circle
          className="tap-org-avatar"
          data-tone={tone}
          cx={x + 8 + r}
          cy={y + h / 2}
          r={r}
        />
        <text
          className="tap-org-avatar-text"
          data-tone={tone}
          x={x + 8 + r}
          y={y + h / 2}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize={pill ? 11 : 12}
        >
          {initialOf(id)}
        </text>
        <text
          className="tap-org-label"
          x={x + 16 + r * 2}
          y={y + h / 2}
          dominantBaseline="central"
          fontSize={pill ? 12.5 : 13}
          fontWeight={pill ? 500 : 600}
        >
          {fit(id, w - 28 - r * 2 - (count === "" ? 0 : COUNT_W), 13)}
        </text>
        {count === "" ? null : (
          <text
            className="tap-org-count"
            x={x + w - 12}
            y={y + h / 2}
            textAnchor="end"
            dominantBaseline="central"
            fontSize={11.5}
          >
            {count}
          </text>
        )}
      </g>
    );
  };

  return (
    <div
      className="tap-org-chart"
      data-selected={selected === null ? "false" : "true"}
    >
      <div className="tap-org-chart-legend">
        {(["owns", "serves"] as const).map((kind) => (
          <span key={kind}>
            <svg aria-hidden="true" width={28} height={8} viewBox="0 0 28 8">
              <line
                x1={0}
                y1={4}
                x2={28}
                y2={4}
                className="tap-org-edge"
                data-kind={kind}
              />
            </svg>
            <span>
              {kind === "owns" ? labels.orgChartOwns : labels.orgChartServes}
            </span>
          </span>
        ))}
        <span className="tap-org-chart-hint">{labels.orgChartHint}</span>
      </div>
      <div className="tap-org-chart-canvas">
        <svg
          role="group"
          aria-label={labels.orgChartLabel}
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
        >
          {/* Trunks: one per column, from the owner down to its last Agent. */}
          {groups.map((group, i) =>
            group.agents.length === 0 ? null : (
              <path
                key={`trunk:${group.owner ?? "platform"}`}
                className="tap-org-trunk"
                data-platform={group.owner === null ? "true" : "false"}
                data-dimmed={
                  group.owner !== null && personDimmed(group.owner)
                    ? "true"
                    : "false"
                }
                d={`M ${colX(i) + TRUNK_X} ${PAD + HEAD_H} V ${
                  agentTop(group.agents.length - 1) + NODE_H / 2 - 6
                } Q ${colX(i) + TRUNK_X} ${
                  agentTop(group.agents.length - 1) + NODE_H / 2
                } ${colX(i) + TRUNK_X + 6} ${
                  agentTop(group.agents.length - 1) + NODE_H / 2
                }`}
              />
            ),
          )}
          {platform.map((agent) => {
            const box = agentBox.get(agent)!;
            const x = colX(box.col) + TRUNK_X;
            return (
              <line
                key={`branch:${agent}`}
                className="tap-org-trunk"
                data-platform="true"
                x1={x}
                x2={box.x}
                y1={box.y + NODE_H / 2}
                y2={box.y + NODE_H / 2}
              />
            );
          })}
          {chart.edges.map((edge) => {
            const key = `${edge.kind}:${edge.from}:${edge.to}`;
            const dimmed = selected !== null && edge.from !== selected;
            const box = agentBox.get(edge.to);
            if (box === undefined) return null;
            if (edge.kind === "owns") {
              const x = colX(box.col) + TRUNK_X;
              return (
                <line
                  key={key}
                  className="tap-org-edge"
                  data-kind="owns"
                  data-edge-kind="owns"
                  data-dimmed={dimmed ? "true" : "false"}
                  x1={x}
                  x2={box.x}
                  y1={box.y + NODE_H / 2}
                  y2={box.y + NODE_H / 2}
                  stroke="var(--tap-brand)"
                  strokeWidth={1.5}
                />
              );
            }
            const from = personAnchor(edge.from, edge.to);
            const to = from === null ? null : agentAnchor(edge.to, from);
            if (from === null || to === null) return null;
            return (
              <line
                key={key}
                className="tap-org-edge"
                data-kind="serves"
                data-edge-kind="serves"
                data-dimmed={dimmed ? "true" : "false"}
                x1={from.x}
                y1={from.y}
                x2={to.x}
                y2={to.y}
                stroke="var(--tap-accent)"
                strokeWidth={1.25}
                strokeDasharray="4 4"
              />
            );
          })}
          {groups.map((group, i) =>
            group.owner === null ? (
              <g key="platform" className="tap-org-platform">
                <rect
                  x={colX(i)}
                  y={PAD}
                  width={COL_W}
                  height={HEAD_H}
                  rx={12}
                />
                <text
                  x={colX(i) + 14}
                  y={PAD + HEAD_H / 2}
                  dominantBaseline="central"
                  fontSize={13}
                  fontWeight={600}
                >
                  {copy.agentTeam.platformOwner}
                </text>
              </g>
            ) : (
              personNode(group.owner, colX(i), PAD, COL_W, HEAD_H, false)
            ),
          )}
          {chart.agents.map((key) => {
            const box = agentBox.get(key);
            if (box === undefined) return null;
            const open = () => onOpenMember(key);
            const label = agentLabel(key);
            const tone = avatarTone(label);
            const w = COL_W - INDENT;
            const stat = agentStats[key];
            const count = countText(stat?.runs);
            return (
              <g
                key={key}
                role="button"
                tabIndex={0}
                className="tap-org-node tap-org-agent"
                aria-label={label}
                aria-description={
                  agentDimmed(key) ? labels.orgChartDimmed : undefined
                }
                data-dimmed={agentDimmed(key) ? "true" : "false"}
                data-related={
                  selected !== null && related.has(key) ? "true" : "false"
                }
                onClick={open}
                onKeyDown={activate(open)}
              >
                <title>{label}</title>
                <rect x={box.x} y={box.y} width={w} height={NODE_H} rx={8} />
                <rect
                  className="tap-org-avatar"
                  data-tone={tone}
                  x={box.x + 7}
                  y={box.y + 7}
                  width={20}
                  height={20}
                  rx={6}
                />
                <text
                  className="tap-org-avatar-text"
                  data-tone={tone}
                  x={box.x + 17}
                  y={box.y + NODE_H / 2}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={11}
                >
                  {initialOf(label)}
                </text>
                {stat?.status === null || stat === undefined ? null : (
                  <circle
                    className="tap-org-status"
                    data-status={stat.status}
                    cx={box.x + 26}
                    cy={box.y + 26}
                    r={3.5}
                  />
                )}
                <text
                  className="tap-org-label"
                  x={box.x + 35}
                  y={box.y + NODE_H / 2}
                  dominantBaseline="central"
                  fontSize={12.5}
                >
                  {fit(label, w - 44 - (count === "" ? 0 : COUNT_W), 12.5)}
                </text>
                {count === "" ? null : (
                  <text
                    className="tap-org-count"
                    x={box.x + w - 10}
                    y={box.y + NODE_H / 2}
                    textAnchor="end"
                    dominantBaseline="central"
                    fontSize={11.5}
                  >
                    {count}
                  </text>
                )}
              </g>
            );
          })}
          {users.length === 0 ? null : (
            <text
              className="tap-org-band-label"
              x={PAD}
              y={bandTop + 6}
              fontSize={11}
              fontWeight={600}
            >
              {labels.orgChartUsers}
            </text>
          )}
          {users.map((id, i) => {
            const p = pillPos(i);
            return personNode(id, p.x, p.y, PILL_W, PILL_H, true);
          })}
        </svg>
      </div>
    </div>
  );
}
