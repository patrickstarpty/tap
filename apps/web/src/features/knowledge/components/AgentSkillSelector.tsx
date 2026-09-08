import { Button } from "antd";
import { useEffect, useMemo, useState } from "react";

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
  onAgentChange: (revisionId: string | null) => void;
  onSkillsChange: (revisionIds: string[]) => void;
}) {
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [retiredSkillsRemoved, setRetiredSkillsRemoved] = useState(false);
  const selected = agents.find((item) => item.revisionId === agentRevisionId);
  const enabledSkillIds = useMemo(
    () => new Set(skills.map((item) => item.revisionId)),
    [skills],
  );
  const activeSkills = skillRevisionIds.filter((id) => enabledSkillIds.has(id));
  const retiredSkills = activeSkills.length !== skillRevisionIds.length;
  useEffect(() => {
    if (retiredSkills) {
      onSkillsChange(activeSkills);
      setRetiredSkillsRemoved(true);
    }
  }, [activeSkills, onSkillsChange, retiredSkills]);
  if (agentRevisionId !== null && selected === undefined)
    return <span role="status">Agent unavailable</span>;
  return (
    <div className="tap-agent-skill-selector">
      <label>
        Agent
        <select
          aria-label="Agent"
          value={agentRevisionId ?? ""}
          onChange={(event) => onAgentChange(event.target.value || null)}
        >
          <option value="">Select an agent</option>
          {agents.map((item) => (
            <option key={item.revisionId} value={item.revisionId}>
              {item.displayName}
            </option>
          ))}
        </select>
      </label>
      {retiredSkillsRemoved && <span role="status">Retired skills were removed</span>}
      <Button
        aria-expanded={skillsOpen}
        onClick={() => setSkillsOpen((open) => !open)}
      >
        Skills
      </Button>
      {skillsOpen && (
        <fieldset aria-label="Skills">
          {skills.map((item) => {
            const checked = activeSkills.includes(item.revisionId);
            return (
              <label key={item.revisionId}>
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() =>
                    onSkillsChange(
                      checked
                        ? activeSkills.filter(
                            (id) => id !== item.revisionId,
                          )
                        : [...activeSkills, item.revisionId],
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
