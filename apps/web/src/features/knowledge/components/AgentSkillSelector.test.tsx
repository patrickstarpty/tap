import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { AgentSkillSelector } from "./AgentSkillSelector";

describe("AgentSkillSelector", () => {
  it("selects only server-returned enabled agent and skill revisions", async () => {
    function Harness() {
      const [agent, setAgent] = useState<string | null>("agent-v1");
      const [skills, setSkills] = useState<string[]>([]);
      return (
        <AgentSkillSelector
          agents={[{ revisionId: "agent-v1", displayName: "Knowledge agent" }]}
          skills={[{ revisionId: "skill-v1", displayName: "Citation skill" }]}
          agentRevisionId={agent}
          skillRevisionIds={skills}
          onAgentChange={setAgent}
          onSkillsChange={setSkills}
        />
      );
    }
    render(<Harness />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Skills" }));
    await user.click(screen.getByRole("checkbox", { name: "Citation skill" }));
    expect(
      screen.getByRole("checkbox", { name: "Citation skill" }),
    ).toBeChecked();
    expect(
      screen.queryByText(/instruction|template|plugin|secret/i),
    ).not.toBeInTheDocument();
  });

  it("fails closed when a selected revision is no longer in the catalog", () => {
    render(
      <AgentSkillSelector
        agents={[]}
        skills={[]}
        agentRevisionId="retired"
        skillRevisionIds={["retired-skill"]}
        onAgentChange={() => {}}
        onSkillsChange={() => {}}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Agent unavailable");
  });

  it("clears retired skills and keeps a null agent controlled", async () => {
    function Harness() {
      const [agent, setAgent] = useState<string | null>(null);
      const [skills, setSkills] = useState<string[]>(["retired"]);
      return <AgentSkillSelector agents={[{ revisionId: "agent-v1", displayName: "Knowledge agent" }]} skills={[]} agentRevisionId={agent} skillRevisionIds={skills} onAgentChange={setAgent} onSkillsChange={setSkills} />;
    }
    render(<Harness />);
    expect(screen.getByRole("combobox", { name: "Agent" })).toHaveValue("");
    expect(screen.getByRole("status")).toHaveTextContent("Retired skills were removed");
    const user = userEvent.setup();
    await user.selectOptions(screen.getByRole("combobox", { name: "Agent" }), "agent-v1");
    expect(screen.getByRole("combobox", { name: "Agent" })).toHaveValue("agent-v1");
  });

  it("keeps selections while loading and offers error and stale-agent recovery", async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    const change = vi.fn();
    const { rerender } = render(<AgentSkillSelector agents={[{ revisionId: "agent-v1", displayName: "Knowledge agent" }]} skills={[]} agentRevisionId="agent-v1" skillRevisionIds={["skill-v1"]} onAgentChange={change} onSkillsChange={() => {}} loading />);
    expect(screen.getByText("Loading approved revisions")).toBeInTheDocument();
    expect(change).not.toHaveBeenCalled();
    rerender(<AgentSkillSelector agents={[]} skills={[]} agentRevisionId={null} skillRevisionIds={[]} onAgentChange={change} onSkillsChange={() => {}} error="Catalog unavailable" onRetry={retry} />);
    await user.click(screen.getByRole("button", { name: "Retry catalog" }));
    expect(retry).toHaveBeenCalledOnce();
    rerender(<AgentSkillSelector agents={[]} skills={[]} agentRevisionId="retired" skillRevisionIds={[]} onAgentChange={change} onSkillsChange={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Clear agent" }));
    expect(change).toHaveBeenLastCalledWith(null);
  });
});
