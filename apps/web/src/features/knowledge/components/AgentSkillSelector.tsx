import { Button } from "antd";
import { useState } from "react";

export interface ApprovedAiAsset {
  readonly revisionId: string;
  readonly displayName: string;
}

export function AgentSkillSelector({
  agents,
  skills,
  agentRevisionId,
  skillRevisionIds,
  onAgentChange,
  onSkillsChange,
}: {
  agents: readonly ApprovedAiAsset[];
  skills: readonly ApprovedAiAsset[];
  agentRevisionId: string | null;
  skillRevisionIds: readonly string[];
  onAgentChange: (revisionId: string) => void;
  onSkillsChange: (revisionIds: string[]) => void;
}) {
  const [skillsOpen, setSkillsOpen] = useState(false);
  const selected = agents.find((item) => item.revisionId === agentRevisionId);
  if (agentRevisionId !== null && selected === undefined)
    return <span role="status">Agent unavailable</span>;
  return (
    <div className="tap-agent-skill-selector">
      <label>
        Agent
        <select
          aria-label="Agent"
          value={agentRevisionId ?? ""}
          onChange={(event) => onAgentChange(event.target.value)}
        >
          {agents.map((item) => (
            <option key={item.revisionId} value={item.revisionId}>
              {item.displayName}
            </option>
          ))}
        </select>
      </label>
      <Button
        aria-expanded={skillsOpen}
        onClick={() => setSkillsOpen((open) => !open)}
      >
        Skills
      </Button>
      {skillsOpen && (
        <fieldset aria-label="Skills">
          {skills.map((item) => {
            const checked = skillRevisionIds.includes(item.revisionId);
            return (
              <label key={item.revisionId}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() =>
                    onSkillsChange(
                      checked
                        ? skillRevisionIds.filter(
                            (id) => id !== item.revisionId,
                          )
                        : [...skillRevisionIds, item.revisionId],
                    )
                  }
                />
                {item.displayName}
              </label>
            );
          })}
        </fieldset>
      )}
    </div>
  );
}
