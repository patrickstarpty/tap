import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
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
});
