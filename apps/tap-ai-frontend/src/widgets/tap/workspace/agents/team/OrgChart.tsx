import { useState, type KeyboardEvent } from "react";

import type { AgentTeamOrgChartView } from "../../../../../features/knowledge/api/agentTeam";
import type { WorkspaceCopy } from "../../copy";

export interface OrgChartProps {
  chart: AgentTeamOrgChartView["data"];
  agentNames: Record<string, string>;
  copy: WorkspaceCopy;
  onOpenMember: (agentKey: string) => void;
}

const ROW = 40;
const PAD = 8;
const WIDTH = 640;
const NODE_W = 200;
const NODE_H = 28;
const LEFT_X = 8;
const RIGHT_X = WIDTH - NODE_W - 8;

function edgeStyle(kind: "owns" | "serves") {
  return kind === "owns"
    ? { stroke: "var(--tap-brand)", strokeWidth: 2 }
    : {
        stroke: "var(--tap-muted)",
        strokeWidth: 1.5,
        strokeDasharray: "5 4",
      };
}

function activate(action: () => void) {
  return (event: KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      action();
    }
  };
}

export function OrgChart({
  chart,
  agentNames,
  copy,
  onOpenMember,
}: OrgChartProps) {
  const labels = copy.agentTeam.team;
  const [selected, setSelected] = useState<string | null>(null);
  if (chart.edges.length === 0)
    return <p className="tap-team-empty">{labels.orgChartEmpty}</p>;

  const agentLabel = (key: string) =>
    agentNames[key] ??
    (key === "tapper_default" ? labels.kinds.tapper_default : key);
  const rowY = (i: number) => PAD + i * ROW;
  const personIndex = new Map(chart.people.map((id, i) => [id, i]));
  const agentIndex = new Map(chart.agents.map((id, i) => [id, i]));
  const height =
    PAD * 2 + Math.max(chart.people.length, chart.agents.length) * ROW;
  const related = new Set<string>();
  if (selected !== null)
    for (const edge of chart.edges)
      if (edge.from === selected) related.add(edge.to);

  return (
    <div className="tap-org-chart">
      <div className="tap-org-chart-legend">
        {(["owns", "serves"] as const).map((kind) => (
          <span key={kind}>
            <svg aria-hidden="true" width={28} height={8} viewBox="0 0 28 8">
              <line x1={0} y1={4} x2={28} y2={4} {...edgeStyle(kind)} />
            </svg>{" "}
            {kind === "owns" ? labels.orgChartOwns : labels.orgChartServes}
          </span>
        ))}
      </div>
      <svg
        role="group"
        aria-label={labels.orgChartLabel}
        viewBox={`0 0 ${WIDTH} ${height}`}
        preserveAspectRatio="xMinYMin meet"
      >
        {chart.edges.map((edge) => {
          const from = personIndex.get(edge.from);
          const to = agentIndex.get(edge.to);
          if (from === undefined || to === undefined) return null;
          const dimmed = selected !== null && edge.from !== selected;
          return (
            <line
              key={`${edge.kind}:${edge.from}:${edge.to}`}
              data-edge-kind={edge.kind}
              data-dimmed={dimmed ? "true" : "false"}
              x1={LEFT_X + NODE_W}
              y1={rowY(from) + NODE_H / 2}
              x2={RIGHT_X}
              y2={rowY(to) + NODE_H / 2}
              {...edgeStyle(edge.kind)}
            />
          );
        })}
        {chart.people.map((id, i) => {
          const toggle = () => setSelected(selected === id ? null : id);
          return (
            <g
              key={id}
              role="button"
              tabIndex={0}
              aria-pressed={selected === id}
              aria-label={id}
              data-dimmed={
                selected !== null && selected !== id ? "true" : "false"
              }
              onClick={toggle}
              onKeyDown={activate(toggle)}
            >
              <rect
                x={LEFT_X}
                y={rowY(i)}
                width={NODE_W}
                height={NODE_H}
                rx={6}
                fill="var(--tap-surface, transparent)"
                stroke="var(--tap-line-strong)"
              />
              <text
                x={LEFT_X + 10}
                y={rowY(i) + NODE_H / 2}
                dominantBaseline="middle"
                fill="var(--tap-ink)"
                fontSize={13}
              >
                {id}
              </text>
            </g>
          );
        })}
        {chart.agents.map((key, i) => {
          const open = () => onOpenMember(key);
          const label = agentLabel(key);
          return (
            <g
              key={key}
              role="button"
              tabIndex={0}
              aria-label={label}
              data-dimmed={
                selected !== null && !related.has(key) ? "true" : "false"
              }
              onClick={open}
              onKeyDown={activate(open)}
            >
              <rect
                x={RIGHT_X}
                y={rowY(i)}
                width={NODE_W}
                height={NODE_H}
                rx={6}
                fill="var(--tap-surface, transparent)"
                stroke="var(--tap-line-strong)"
              />
              <text
                x={RIGHT_X + 10}
                y={rowY(i) + NODE_H / 2}
                dominantBaseline="middle"
                fill="var(--tap-ink)"
                fontSize={13}
              >
                {label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
