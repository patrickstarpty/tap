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
  loading = false,
  error = null,
  onRetry,
}: {
  agents: readonly ApprovedAiAsset[];
  skills: readonly ApprovedAiAsset[];
  agentRevisionId: string | null;
  skillRevisionIds: readonly string[];
  onAgentChange: (revisionId: string | null) => void;
  onSkillsChange: (revisionIds: string[]) => void;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}) {
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [retiredSkillsRemoved, setRetiredSkillsRemoved] = useState(false);
  const selected = agents.find((item) => item.revisionId === agentRevisionId);
  const enabledSkillIds = useMemo(
    () => new Set(skills.map((item) => item.revisionId)),
    [skills],
  );
  const activeSkills = skillRevisionIds.filter((id) => enabledSkillIds.has(id));
  const retiredSkills = !loading && activeSkills.length !== skillRevisionIds.length;
  useEffect(() => {
    if (retiredSkills) {
      onSkillsChange(activeSkills);
      setRetiredSkillsRemoved(true);
    }
  }, [activeSkills, onSkillsChange, retiredSkills]);
  if (error)
    return <div role="alert">{error}{onRetry && <Button onClick={onRetry}>Retry catalog</Button>}</div>;
  if (agentRevisionId !== null && selected === undefined)
    return <div role="status">Agent unavailable <Button onClick={() => onAgentChange(null)}>Clear agent</Button></div>;
  return (
    <div className="tap-agent-skill-selector">
      <label>
        Agent
        <select
          aria-label="Agent"
          value={agentRevisionId ?? ""}
          disabled={loading || agents.length === 0}
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
      {loading && <span role="status">Loading approved revisions</span>}
      {!loading && agents.length === 0 && <span role="status">No approved agents available</span>}
      {retiredSkillsRemoved && <span role="status">Retired skills were removed</span>}
      <Button
        aria-expanded={skillsOpen}
        disabled={loading || skills.length === 0}
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
